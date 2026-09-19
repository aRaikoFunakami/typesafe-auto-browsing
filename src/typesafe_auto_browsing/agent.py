"""Observe -> judge -> act loop.

TypeSafe judges whether the goal is done, which tool to call next (any tool the schema allows,
including browser_snapshot to look closer at an element) and every argument of the call. Nothing is
generated: values are chosen among the choices, the parts of the goal and the text of the page, and
copied. Playwright MCP output and tool schemas are used as they are.
"""

import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from mcp import ClientSession
from mcp.types import Tool
from typesafe_sdk import Choice, Noul

from .arguments import Context, Decision, decide, fit, is_ref, modal_handlers, state_of, unusable_reason
from .answer import ANSWER_ATTEMPTS, MIN_ANSWER_CONFIDENCE, SETTLE_SECONDS, Answers, find_answers
from .page_view import look
from .playwright_mcp import call_tool
from .usage import MeteredClient

SNAPSHOT = "browser_snapshot"
CLOSE = "browser_close"
ATTACH_CHARS = 20_000  # longer tool output is stored as a file by the trace
FOCUS_CHARS = 8_000  # longest output of a read-only tool shown to TypeSafe (the trace keeps all of it)
MAX_RETRIES = 3  # tools chosen again in one step when the chosen one cannot be used
MAX_DEAD_STEPS = 3  # steps in a row in which no tool could be used
ERROR_LINES = 4  # lines of a tool's error shown to TypeSafe as the reason it failed
_PAGE = re.compile(r"- Page (?:URL|Title): .*")

Log = Callable[[str], None]
Confirm = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True)
class Settings:
    max_steps: int = 20
    done_threshold: float = 0.8
    page_chars: int = 50_000


@dataclass(frozen=True)
class Outcome:
    success: bool
    reason: str
    page: str = ""  # the last page that could be read
    history: tuple[str, ...] = ()


async def _call(client: MeteredClient, session: ClientSession, name: str, arguments: dict):
    """call_tool, recording the call and its complete output."""
    client.trace.event("mcp_call", tool=name, arguments=arguments)
    started = time.monotonic()
    text, is_error = await call_tool(session, name, arguments)
    duration = round(time.monotonic() - started, 3)
    if len(text) > ATTACH_CHARS:  # e.g. a page snapshot: keep it whole in a file next to the trace
        file = client.trace.attach(f"{client.trace.next_seq:04d}-{name}.yml", text)
        client.trace.event("mcp_result", tool=name, is_error=is_error, chars=len(text), text_file=file, duration_s=duration)
    else:
        client.trace.event("mcp_result", tool=name, is_error=is_error, text=text, duration_s=duration)
    return text, is_error


def read_only(tool: Tool) -> bool:
    return bool(tool.annotations and getattr(tool.annotations, "read_only_hint", None))


def _digest(page: str) -> str:
    """The URL and title of the page: what tells two pages apart."""
    return "\n".join(_PAGE.findall(page[:2_000]))


def _tool_question(tools: dict[str, Tool]) -> Choice:
    return Choice(
        instructions=(
            "Which browser tool should be called next to make progress toward `goal`, given "
            "`history` and the current `page`?"
        ),
        criteria={name: t.description for name, t in tools.items()},
    )


def _error_reason(text: str) -> str:
    lines = [line.strip() for line in text.removeprefix("### Error").strip().splitlines() if line.strip()]
    return " | ".join(lines[:ERROR_LINES])[:400]


def _describe(action: str, decision: Decision, page: str) -> str:
    """What is about to happen, for a person to confirm."""
    notes: list[str] = []

    def add(arguments: dict, sources: dict, indent: str = "") -> None:
        for name, value in arguments.items():
            if is_ref(name):
                line = next((l.strip() for l in page.splitlines() if f"[ref={value}]" in l), "")
                notes.append(f"{indent}{name} = {line[:100]}")
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                for entry in value:
                    add(entry, {}, indent + "  ")
                    notes.append(f"{indent}  --")
            elif sources.get(name) == "page":
                notes.append(f"{indent}{name} is text taken from the page")

    add(decision.arguments, decision.sources)
    return action + "".join(f"\n      {note}" for note in notes)


DONE = Noul(
    instructions=(
        "The goal in `goal` has been achieved: the current `page` already "
        "shows the outcome the goal asks for, or, when the goal asks to find something, the "
        "information it is found from (the page need not point out the answer)."
    ),
    criteria={
        "true": "The page shows the requested outcome itself, such as the results, or the information to find the answer in.",
        "false": (
            "More steps are needed: the site is not open yet, fields are "
            "still empty, or a search has not been submitted yet."
        ),
    },
)


async def run(
    client: MeteredClient,
    session: ClientSession,
    tools: list[Tool],
    goal: str,
    settings: Settings,
    log: Log = print,
    confirm: Confirm | None = None,
) -> Outcome:
    offered: dict[str, Tool] = {}
    for tool in tools:
        if reason := unusable_reason(tool):
            log(f"(not offered: {tool.name}: {reason})")
        else:
            offered[tool.name] = tool
    if not offered:
        return Outcome(False, "no tool can be used")

    history: list[str] = []
    attempts: Counter[tuple[str, str]] = Counter()
    failed: dict[str, set[str]] = defaultdict(set)  # refs a tool failed on, until the page changes
    page_chars = settings.page_chars
    snapshot = ""  # the last page that could be read
    focus = ""  # output of the last read-only tool
    last_output = ""
    dead = 0
    for step in range(1, settings.max_steps + 1):
        text, is_error = await _call(client, session, SNAPSHOT, {})
        handlers = modal_handlers(text) if is_error else []
        if not is_error or not handlers:
            snapshot = text
        extra: dict = {}
        if handlers:  # a dialog or file chooser is open: only its tool can act, and the page is not readable
            extra["modal"] = text
        if focus:
            extra["focus"] = focus

        view = await look(client, goal, history, snapshot, page_chars)
        page = view.text
        if view.parts > 1:
            log(f"    page: {len(snapshot):,} chars; showing parts {view.shown} of {view.parts}")
        available = {n: t for n, t in offered.items() if n in handlers} if handlers else offered
        available = available or offered

        start = min(page_chars, len(page))
        response, _, limit = await fit(
            client,
            page,
            start,
            lambda t: (state_of(goal, history, t, **extra), {"done": DONE, "tool": _tool_question(available)}),
        )
        # TypeSafe rejected the longer page: remember what fit, and try a longer one again later.
        page_chars = limit if limit < start else min(settings.page_chars, page_chars * 2)
        done = response.nouls["done"].noul
        if history and done >= settings.done_threshold:
            return Outcome(True, f"goal achieved (p={done:.2f})", snapshot, tuple(history))

        pick = response.choices["tool"]
        log(f"[{step}] typesafe: {pick.choice}  (tool confidence {pick.confidence:.2f}, done p={done:.2f})")
        tool: Tool | None = None
        decision = Decision({}, {})
        excluded: dict[str, str] = {}
        for attempt in range(MAX_RETRIES + 1):
            candidate = available[pick.choice]
            context = Context(
                client, goal, history, page, limit, log, extra, last_output, frozenset(failed[candidate.name])
            )
            decision = await decide(context, candidate)
            if not decision.unusable:
                tool = candidate
                break
            excluded[candidate.name] = decision.unusable
            log(f"    {candidate.name} cannot be used now: {decision.unusable}")
            remaining = {n: t for n, t in available.items() if n not in excluded}
            if not remaining or attempt == MAX_RETRIES:
                break
            again, _, _ = await fit(
                client, page, limit, lambda t: (state_of(goal, history, t, **extra), {"tool": _tool_question(remaining)})
            )
            pick = again.choices["tool"]
            log(f"    typesafe: {pick.choice}  (tool confidence {pick.confidence:.2f})")

        if tool is None:
            dead += 1
            reasons = "; ".join(f"{n}: {r}" for n, r in excluded.items())
            history.append(f"(no tool could be used: {reasons})")
            if dead >= MAX_DEAD_STEPS:
                return Outcome(False, f"no tool could be used for {dead} steps in a row ({reasons})")
            continue
        dead = 0

        arguments = decision.arguments
        action = f"{tool.name} {json.dumps(arguments, ensure_ascii=False)}"
        key = (action, _digest(snapshot))
        attempts[key] += 1
        if attempts[key] > 2:
            return Outcome(False, f"stuck: repeated {action}")
        if confirm and not read_only(tool):
            if not await confirm(_describe(action, decision, page)):
                log(f"    declined: {action}")
                history.append(f"{action} -> DECLINED by the user")
                continue
        log(f"    mcp: {action}")
        output, is_error = await _call(client, session, tool.name, arguments)
        last_output = output
        if is_error:
            reason = _error_reason(output)
            log(f"    mcp: failed: {reason}")
            history.append(f"{action} -> FAILED: {reason}")
            failed[tool.name].update(str(v) for n, v in arguments.items() if is_ref(n))
            continue
        log("    mcp: ok")
        history.append(action)
        if tool.name == CLOSE:
            return Outcome(False, "the browser was closed")
        if read_only(tool):
            # Its output is what was asked for; a snapshot of the whole page is retaken every step anyway.
            focus = "" if tool.name == SNAPSHOT and "target" not in arguments else output[:FOCUS_CHARS]
        else:
            focus = ""
            failed.clear()
    return Outcome(False, f"step limit ({settings.max_steps}) reached")


async def answer(client: MeteredClient, session: ClientSession, goal: str, outcome: Outcome, log: Log) -> Answers:
    """The answer to the goal, read from the page as it is now.

    The page the run ended on can still be loading (a search that was just sorted), so the page is read
    again for the answer, and when the answer is doubtful the page is read again after a short wait."""
    page = outcome.page
    answers = Answers(False, "not asked")
    for attempt in range(ANSWER_ATTEMPTS):
        text, is_error = await _call(client, session, SNAPSHOT, {})
        if not is_error:
            page = text
        answers = await find_answers(client, goal, list(outcome.history), page)
        if not answers.asked or (answers.items and answers.items[0].confidence >= MIN_ANSWER_CONFIDENCE):
            break
        if attempt + 1 < ANSWER_ATTEMPTS:
            log(f"    answer: not sure yet ({answers.reason}); the page may still be loading, reading it again")
            await _call(client, session, "browser_wait_for", {"time": SETTLE_SECONDS})
    return answers
