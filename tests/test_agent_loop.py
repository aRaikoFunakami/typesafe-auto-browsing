"""The observe -> judge -> act loop, with TypeSafe and Playwright MCP replaced by scripts."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from mcp.types import Tool
from typesafe_sdk import Choice

from typesafe_auto_browsing.agent import Settings, run
from typesafe_auto_browsing.arguments import NONE

TOOLS = [Tool(**t) for t in json.loads((Path(__file__).parent / "fixtures" / "tools.json").read_text())]
PAGE = '### Page\n- Page URL: https://x/\n### Snapshot\n```yaml\n- generic [ref=e1]:\n  - button "OK" [ref=e2]\n  - button "Other" [ref=e3]\n```'
MODAL = '### Error\nError: Tool "browser_snapshot" does not handle the modal state.\n### Modal state\n- ["alert" dialog]: can be handled by browser_handle_dialog'


class Session:
    """Answers tool calls from `respond(name, arguments) -> (text, is_error)`."""

    def __init__(self, respond):
        self.respond, self.calls = respond, []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        text, is_error = self.respond(name, arguments, len(self.calls))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], is_error=is_error)


class TypeSafe:
    """Picks `tools` in order, the first ref that is offered, and says done after `finish_after` actions."""

    def __init__(self, tools, finish_after=99, ref=None):
        self.tools, self.finish_after, self.ref = list(tools), finish_after, ref
        self.requests = []
        self.usage = None
        self.trace = SimpleNamespace(event=lambda *a, **k: None, attach=lambda *a: "f", next_seq=1)

    async def system_one(self, state, questions, **_):
        self.requests.append((state, questions))
        choices, nouls = {}, {}
        for name, question in questions.items():
            if isinstance(question, Choice):
                options = list(question.criteria)
                if name == "tool":
                    choice = next((t for t in self.tools if t in options), options[0])
                    if choice in self.tools:
                        self.tools.remove(choice)
                elif name.startswith("target"):
                    choice = self.ref if self.ref in options else options[0]
                else:
                    choice = next((o for o in options if o != NONE), NONE)
                choices[name] = SimpleNamespace(choice=choice, confidence=0.9, probabilities={choice: 0.9})
            elif name == "done":
                nouls[name] = SimpleNamespace(noul=0.9 if len(state["history"]) >= self.finish_after else 0.1)
            else:
                nouls[name] = SimpleNamespace(noul=0.1)
        return SimpleNamespace(choices=choices, nouls=nouls)


def go(session, client, confirm=None, **settings):
    import typesafe_auto_browsing.agent as agent

    async def call_tool(_session, name, arguments):
        result = await session.call_tool(name, arguments)
        text = "".join(c.text for c in result.content)
        return text, result.is_error or text.startswith("### Error")

    logs: list[str] = []
    original, agent.call_tool = agent.call_tool, call_tool
    try:
        outcome = asyncio.run(run(client, session, TOOLS, "goal", Settings(**settings), logs.append, confirm))
    finally:
        agent.call_tool = original
    return outcome, logs


def tool_options(client, step):
    return [list(q["tool"].criteria) for _, q in client.requests if "tool" in q][step]


def test_a_dialog_leaves_only_the_tool_that_can_handle_it():
    def respond(name, arguments, n):
        if name == "browser_snapshot":
            return (MODAL, True) if n >= 3 else (PAGE, False)
        return ("### Result\nok", False)

    client = TypeSafe(["browser_click", "browser_handle_dialog"], finish_after=2, ref="e2")
    outcome, _ = go(Session(respond), client)
    assert tool_options(client, 1) == ["browser_handle_dialog"]
    assert outcome.success


def test_a_failed_element_is_not_offered_again_on_the_same_page():
    def respond(name, arguments, n):
        if name == "browser_click":
            return ("### Error\nTimeoutError: element is outside of the viewport", True)
        return (PAGE, False)

    client = TypeSafe(["browser_click", "browser_click"], finish_after=99, ref="e2")
    outcome, logs = go(Session(respond), client, max_steps=2)
    refs = [list(q[k].criteria) for _, q in client.requests for k in q if k.startswith("target")]
    assert "e2" in refs[0] and "e2" not in refs[1]
    assert any("FAILED" in " ".join(s["history"]) for s, _ in client.requests[2:])
    assert not outcome.success


def test_the_error_cause_is_given_to_typesafe():
    client = TypeSafe(["browser_click"], ref="e2")
    go(Session(lambda n, a, i: ("### Error\nTimeoutError: x\nCall log:\n - element is outside of the viewport", True) if n == "browser_click" else (PAGE, False)), client, max_steps=2)
    history = client.requests[-1][0]["history"]
    assert "outside of the viewport" in " ".join(history)


def test_no_usable_tool_for_three_steps_fails_instead_of_looping():
    # Only tools whose values have no candidate are chosen: each is excluded, then the next is chosen.
    client = TypeSafe(["browser_navigate", "browser_press_key", "browser_wait_for", "browser_find"] * 6)
    original = client.system_one

    async def no_values(state, questions, **kw):
        response = await original(state, questions, **kw)
        for name, question in questions.items():
            if isinstance(question, Choice) and ":" in name and not name.startswith("target"):
                response.choices[name] = SimpleNamespace(choice=NONE, confidence=0.9, probabilities={NONE: 0.9})
        return response

    client.system_one = no_values
    outcome, logs = go(Session(lambda n, a, i: (PAGE, False)), client, max_steps=10)
    assert not outcome.success and "no tool could be used for 3 steps in a row" in outcome.reason
    assert any("cannot be used now" in line for line in logs)


def test_a_declined_action_is_not_run_and_is_remembered():
    async def decline(_description):
        return False

    session = Session(lambda n, a, i: (PAGE, False))
    client = TypeSafe(["browser_click"], ref="e2")
    outcome, _ = go(session, client, confirm=decline, max_steps=2)
    assert [c for c in session.calls if c[0] == "browser_click"] == []
    assert "DECLINED" in " ".join(client.requests[-1][0]["history"])


def test_read_only_tools_are_not_confirmed():
    asked = []

    async def confirm(description):
        asked.append(description)
        return True

    client = TypeSafe(["browser_snapshot"], finish_after=1)
    go(Session(lambda n, a, i: (PAGE, False)), client, confirm=confirm, max_steps=3)
    assert asked == []


def test_closing_the_browser_ends_the_run():
    client = TypeSafe(["browser_close"])
    outcome, _ = go(Session(lambda n, a, i: (PAGE, False)), client, max_steps=5)
    assert outcome.reason == "the browser was closed"


def test_the_answer_is_read_from_a_fresh_page_and_again_while_it_is_doubtful(monkeypatch):
    import typesafe_auto_browsing.agent as agent
    from typesafe_auto_browsing.answer import Answers, Candidate

    pages = ["loading page", "settled page"]
    seen = []

    async def call_tool(_session, name, arguments):
        if name == "browser_snapshot":
            return pages.pop(0), False
        return "ok", False

    async def find_answers(_client, _goal, _history, page):
        seen.append(page)
        confidence = 0.2 if page == "loading page" else 0.9
        return Answers(True, "answered", [Candidate("x", confidence, confidence, 0)])

    monkeypatch.setattr(agent, "call_tool", call_tool)
    monkeypatch.setattr(agent, "find_answers", find_answers)
    client = TypeSafe([])
    outcome = agent.Outcome(True, "done", "the page the run ended on", ("navigate",))
    result = asyncio.run(agent.answer(client, Session(lambda *a: ("", False)), "goal", outcome, lambda _l: None))
    assert seen == ["loading page", "settled page"] and result.candidates[0].confidence == 0.9


def test_a_page_that_has_not_changed_is_not_asked_again(monkeypatch):
    import typesafe_auto_browsing.agent as agent
    from typesafe_auto_browsing.answer import Answers, Candidate

    asked = []

    async def call_tool(_session, name, arguments):
        return "the same page", False

    async def find_answers(_client, _goal, _history, page):
        asked.append(page)
        return Answers(True, "answered", [Candidate("x", 0.3, 0.3, 0)])  # doubtful, but nothing is loading

    monkeypatch.setattr(agent, "call_tool", call_tool)
    monkeypatch.setattr(agent, "find_answers", find_answers)
    outcome = agent.Outcome(True, "done", "p", ())
    result = asyncio.run(agent.answer(TypeSafe([]), None, "goal", outcome, lambda _l: None))
    assert asked == ["the same page"] and result.candidates[0].text == "x"  # read once, the doubt is reported


def test_an_operation_goal_is_not_retried(monkeypatch):
    import typesafe_auto_browsing.agent as agent
    from typesafe_auto_browsing.answer import Answers

    calls = []

    async def call_tool(_session, name, arguments):
        calls.append(name)
        return "page", False

    async def find_answers(*_):
        return Answers(False, "the goal asks for an operation")

    monkeypatch.setattr(agent, "call_tool", call_tool)
    monkeypatch.setattr(agent, "find_answers", find_answers)
    outcome = agent.Outcome(True, "done", "p", ())
    asyncio.run(agent.answer(TypeSafe([]), None, "goal", outcome, lambda _l: None))
    assert calls == ["browser_snapshot"]  # one fresh page, no waiting
