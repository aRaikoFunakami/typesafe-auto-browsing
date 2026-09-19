import asyncio
from types import SimpleNamespace

from typesafe_auto_browsing.page_view import _label, _parts, look


def test_short_page_is_shown_whole_and_unchanged():
    page = "- generic [ref=e1]:\n  - link \"a\" [ref=e2]\n    - /url: https://x/?long=1"
    view = asyncio.run(look(None, "goal", [], page, 50_000))
    assert view.text == page and view.parts == 1


def test_parts_cover_the_page_exactly():
    page = "\n".join(f'  - link "item {i}" [ref=e{i}]' for i in range(3000))
    parts = _parts(page)
    assert "\n".join(parts) == page and len(parts) > 1


def test_long_page_shows_chosen_parts_in_order_and_unchanged():
    page = "\n".join(f'  - link "item {i}" [ref=e{i}]' for i in range(3000))
    parts = _parts(page)

    class Client:
        trace = SimpleNamespace(event=lambda *a, **k: None)

        async def system_one(self, state, questions):
            def answer(top):
                probabilities = {f"part {i}": 0.0 for i in range(len(parts))}
                probabilities[f"part {top}"] = 1.0
                return SimpleNamespace(probabilities=probabilities)

            return SimpleNamespace(choices={"outcome": answer(len(parts) - 1), "control": answer(0)})

    view = asyncio.run(look(Client(), "goal", [], page, 10_000))
    assert view.shown[0] == 0 and view.shown[-1] == len(parts) - 1  # both questions' first choices, in page order
    assert parts[0] in view.text and parts[-1] in view.text and "omitted" in view.text


def test_label_names_controls_first():
    part = '  - generic:\n    - link "aaa" [ref=e1]\n    - combobox "並べ替え" [ref=e2]\n    - heading "結果" [ref=e3]'
    assert _label(part).startswith("controls: combobox 並べ替え")
