import asyncio
from types import SimpleNamespace

from typesafe_auto_browsing.page_view import split_parts, view_page


def test_short_page_is_shown_whole_and_unchanged():
    page = "- generic [ref=e1]:\n  - link \"a\" [ref=e2]\n    - /url: https://x/?long=1"
    view = asyncio.run(view_page(None, "goal", [], page, 50_000))
    assert view.text == page and view.parts == 1


def test_parts_cover_the_page_exactly():
    page = "\n".join(f'  - link "item {i}" [ref=e{i}]' for i in range(3000))
    parts = split_parts(page)
    assert "\n".join(parts) == page and len(parts) > 1


def _client(outcome, control):
    """部分ごとに、その中身で答える TypeSafe: `outcome` / `control` は、部分の中の目印から確率への対応。"""

    class Client:
        trace = SimpleNamespace(event=lambda *a, **k: None)

        async def system_one(self, state, questions):
            def noul(table):
                return SimpleNamespace(noul=next((p for marker, p in table.items() if marker in state["page"]), 0.0))

            return SimpleNamespace(nouls={"outcome": noul(outcome), "control": noul(control)})

    return Client()


def test_long_page_shows_parts_that_hold_the_outcome_or_control_in_order_and_unchanged():
    page = "\n".join(f'  - link "item {i}" [ref=e{i}]' for i in range(3000))
    parts = split_parts(page)
    last = '"item 2999"'

    view = asyncio.run(view_page(_client({last: 0.9}, {'"item 0"': 0.8}), "goal", [], page, 40_000))
    assert view.shown == [0, len(parts) - 1]
    assert parts[0] in view.text and parts[-1] in view.text and "omitted" in view.text


def test_a_button_in_a_middle_part_is_shown():  # logs/20260920-082014 の失敗: 検索ボタンが、見えていない部分にあった
    page = "\n".join(f'  - link "item {i}" [ref=e{i}]' for i in range(3000))
    parts = split_parts(page)
    marker = '"item 1500"'
    middle = next(i for i, part in enumerate(parts) if marker in part)

    view = asyncio.run(view_page(_client({}, {marker: 0.9}), "goal", [], page, 40_000))
    assert view.shown == [middle]


def test_shown_parts_stay_within_the_limit():
    page = "\n".join(f'  - link "item {i}" [ref=e{i}]' for i in range(3000))
    view = asyncio.run(view_page(_client({"item": 0.9}, {}), "goal", [], page, 20_000))
    assert len(view.text) <= 20_000 + len("... (part of the page omitted) ...") * len(view.shown)


def test_the_most_likely_part_is_shown_even_when_none_is_likely():
    page = "\n".join(f'  - link "item {i}" [ref=e{i}]' for i in range(3000))
    view = asyncio.run(view_page(_client({'"item 5"': 0.1}, {}), "goal", [], page, 40_000))
    assert view.shown == [0]
