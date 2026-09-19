import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import Tool
from typesafe_sdk import Choice

from typesafe_auto_browsing.agent import _page_id, is_read_only
from typesafe_auto_browsing.arguments import (
    NONE,
    Context,
    decide,
    goal_candidates,
    is_ref,
    modal_handlers,
    options_under,
    page_names,
    unusable_reason,
)
from typesafe_auto_browsing.keys import KEYS

TOOLS = {t["name"]: Tool(**t) for t in json.loads((Path(__file__).parent / "fixtures" / "tools.json").read_text())}
GOAL = "https://www.amazon.co.jp/ で一番やすいusb-cケーブルをさがして"

PAGE = """\
- generic [ref=e1]:
  - textbox "検索" [ref=e2]
  - button "検索する" [ref=e3]
  - combobox "並べ替え::" [ref=e4]:
    - option "おすすめ"
    - 'option "価格: 安い順" [selected]'
    - option "新着"
  - link "USB-C ケーブル 1m" [ref=e5]
"""


class FakeClient:
    """Answers every Choice with `pick(options)` and every Noul with `noul`."""

    def __init__(self, pick, noul=0.1):
        self.pick, self.noul, self.requests = pick, noul, []

    async def system_one(self, state, questions, **_):
        self.requests.append((state, questions))
        choices = {}
        for name, question in questions.items():
            if isinstance(question, Choice):
                options = list(question.criteria)
                choice = self.pick(name, options)
                choices[name] = SimpleNamespace(choice=choice, confidence=0.9, probabilities={choice: 0.9})
        nouls = {
            n: SimpleNamespace(noul=self.noul(n) if callable(self.noul) else self.noul)
            for n, q in questions.items()
            if not isinstance(q, Choice)
        }
        return SimpleNamespace(choices=choices, nouls=nouls)


def run_decide(tool, pick, page=PAGE, goal=GOAL, noul=0.1, **kwargs):
    logs: list[str] = []
    client = FakeClient(pick, noul)
    context = Context(client, goal, [], page, 50_000, logs.append, **kwargs)
    return asyncio.run(decide(context, TOOLS[tool])), client


def prefer(*wanted):
    """Pick the first wanted option that is offered, else NONE."""

    def pick(_name, options):
        return next((w for w in wanted if w in options), NONE)

    return pick


def test_only_code_is_unusable_from_the_schema():
    reasons = {name: unusable_reason(tool) for name, tool in TOOLS.items()}
    assert {n for n, r in reasons.items() if r} == {"browser_evaluate"}
    assert len(TOOLS) == 25


def test_refs_include_drag_targets():
    assert all(is_ref(n) for n in ("target", "ref", "startTarget", "endTarget"))
    assert not is_ref("text")


def test_read_only_comes_from_mcp_annotations():
    assert is_read_only(TOOLS["browser_snapshot"]) and not is_read_only(TOOLS["browser_click"])


def test_goal_candidates_are_exact_parts_of_the_goal():
    candidates = goal_candidates(GOAL)
    assert {"usb-cケーブル", "https://www.amazon.co.jp/", "一番", "usb-c", "ケーブル"} <= set(candidates)
    assert all(c in GOAL for c in candidates)
    assert {"横浜", "青森"} <= set(goal_candidates("横浜から青森までを検索して"))


def test_page_names_and_options():
    assert "USB-C ケーブル 1m" in page_names(PAGE)
    assert options_under(PAGE, "e4") == ["おすすめ", "価格: 安い順", "新着"]
    assert options_under(PAGE, "e999") == []


def test_modal_state_names_the_tool_that_can_handle_it():
    text = '### Modal state\n- ["alert" dialog with message "hi"]: can be handled by browser_handle_dialog\n### Snapshot'
    assert modal_handlers(text) == ["browser_handle_dialog"]
    assert modal_handlers("### Page\n- Page URL: x") == []


def test_digest_is_the_url_and_title():
    assert _page_id("### Page\n- Page URL: https://a\n- Page Title: T\n### Snapshot") == "- Page URL: https://a\n- Page Title: T"


def test_navigate_takes_the_url_from_the_goal():
    decision, _ = run_decide("browser_navigate", prefer("https://www.amazon.co.jp/"))
    assert decision.arguments == {"url": "https://www.amazon.co.jp/"}
    assert decision.sources == {"url": "goal"}


def test_type_chooses_element_then_text_and_copies_it():
    def pick(name, options):
        if name.startswith("target"):
            return "e2"
        return "usb-cケーブル" if "usb-cケーブル" in options else NONE

    decision, client = run_decide("browser_type", pick)
    assert decision.arguments["target"] == "e2" and decision.arguments["text"] == "usb-cケーブル"
    # the text is asked after the element is known
    assert client.requests[-1][0]["next_action"] == {"tool": "browser_type", "target": "e2"}


def test_text_of_the_page_is_used_only_when_the_goal_has_no_value():
    def pick(name, options):
        if name.startswith("target"):
            return "e2"
        return "USB-C ケーブル 1m" if "USB-C ケーブル 1m" in options else NONE

    decision, _ = run_decide("browser_type", pick)
    assert decision.arguments["text"] == "USB-C ケーブル 1m" and decision.sources["text"] == "page"


def test_required_value_without_candidate_makes_the_tool_unusable():
    decision, _ = run_decide("browser_navigate", prefer())
    assert decision.unusable == "no value for url"


def test_tool_with_only_optional_arguments_needs_one_decided():
    decision, _ = run_decide("browser_wait_for", prefer())
    assert decision.unusable == "nothing to specify"
    decision, _ = run_decide(
        "browser_wait_for", lambda name, options: "3" if name.startswith("time") else NONE, goal="3秒待って"
    )
    assert decision.arguments == {"time": 3}


def test_select_option_chooses_among_the_options_of_the_element():
    def pick(name, options):
        return "e4" if name.startswith("target") else ("価格: 安い順" if "価格: 安い順" in options else NONE)

    decision, _ = run_decide("browser_select_option", pick)
    assert decision.arguments == {"target": "e4", "values": ["価格: 安い順"]}


def test_press_key_offers_playwright_key_names():
    decision, _ = run_decide("browser_press_key", prefer("ArrowLeft"))
    assert "ArrowLeft" in KEYS and decision.arguments == {"key": "ArrowLeft"}


def test_failed_refs_are_not_offered_again():
    seen: list[list[str]] = []

    def pick(name, options):
        seen.append(options)
        return options[0]

    run_decide("browser_click", pick, failed_refs=frozenset({"e2"}))
    assert all("e2" not in options for options in seen[:1])


def test_many_refs_are_chosen_in_two_rounds():
    page = "\n".join(f'  - button "b{i}" [ref=e{i}]' for i in range(600))
    decision, client = run_decide("browser_hover", prefer("e599", "e0"), page=page)
    assert decision.arguments == {"target": "e599"}
    assert len(client.requests) == 2  # chunk winners, then the final


def test_optional_element_of_snapshot_can_be_left_out():
    decision, _ = run_decide("browser_snapshot", prefer())
    assert decision.arguments == {} and decision.unusable is None
    decision, _ = run_decide("browser_snapshot", prefer("e4"))
    assert decision.arguments == {"target": "e4"}


def test_click_modifiers_are_flags():
    decision, _ = run_decide("browser_click", lambda n, o: "e3" if n.startswith("target") else NONE, noul=0.9)
    assert decision.arguments["modifiers"] and decision.arguments["doubleClick"] is True


def test_trace_files_are_private(tmp_path):
    from typesafe_auto_browsing.trace import Trace

    trace = Trace(tmp_path / "logs")
    name = trace.attach("a.yml", "x")
    trace.event("k")
    trace.close()
    assert (trace.path.stat().st_mode & 0o777) == 0o600
    assert ((tmp_path / "logs" / name).stat().st_mode & 0o777) == 0o600
    assert ((tmp_path / "logs").stat().st_mode & 0o777) == 0o700


FORM = """\
- generic [ref=e1]:
  - textbox "名前" [ref=e2]
  - textbox "メール" [ref=e3]
  - button "送信" [ref=e4]
"""


def test_fill_form_fills_entry_by_entry_with_the_names_of_the_elements():
    calls = {"more": 0, "target": 0}
    offered: list[list[str]] = []

    def noul(name):
        if name == "more":
            calls["more"] += 1
            return 0.9 if calls["more"] == 1 else 0.1
        return 0.1

    def pick(name, options):
        if name.startswith("target"):
            calls["target"] += 1
            offered.append(options)
            return "e2" if calls["target"] == 1 else "e3"
        if name.startswith("type"):
            return "textbox"
        value = "太郎" if calls["target"] == 1 else "taro@example.com"
        return value if value in options else NONE

    decision, client = run_decide(
        "browser_fill_form", pick, page=FORM, goal="名前に太郎、メールに taro@example.com を入力して", noul=noul
    )
    assert decision.unusable is None
    assert "e2" in offered[0] and "e2" not in offered[1]  # an element is filled once
    assert decision.arguments["fields"] == [
        {"target": "e2", "name": "名前", "type": "textbox", "value": "太郎"},
        {"target": "e3", "name": "メール", "type": "textbox", "value": "taro@example.com"},
    ]


TABS = """\
### Open tabs
- 0: (current) [](data:text/html,x)
- 1: [Example Domain](https://example.com/)
### Page
- Page URL: data:text/html,x
"""


def test_tab_index_is_chosen_among_the_numbers_the_tab_list_shows():
    def pick(name, options):
        if name.startswith("action"):
            return "select"
        return "1" if "1" in options else NONE

    decision, _ = run_decide("browser_tabs", pick, page=TABS, goal="開いた新しいタブに切り替えて")
    assert decision.arguments == {"action": "select", "index": 1}
    assert decision.sources["index"] == "list"


def test_network_request_index_comes_from_the_listed_requests():
    decision, _ = run_decide(
        "browser_network_request",
        lambda n, o: "2" if n.startswith("index") and "2" in o else NONE,
        page=FORM,
        goal="2番目のリクエストの中身を見て",
        last_output="### Result\n1. [GET] https://a/ => [200] OK\n2. [POST] https://b/ => [500] Error",
    )
    assert decision.arguments["index"] == 2


def test_arguments_the_schema_says_not_to_give_together_are_not_both_given():
    from typesafe_auto_browsing.arguments import exclusive_pairs

    assert exclusive_pairs(TOOLS["browser_find"].input_schema["properties"]) == [("text", "regex")]
    decision, _ = run_decide("browser_find", lambda n, o: "￥29" if "￥29" in o else NONE, page='  - link "￥29" [ref=e1]', goal="￥29 を探して")
    assert decision.arguments == {"text": "￥29"}
