"""What TypeSafe gets to see of a page.

TypeSafe reads at most ~32k tokens, but a snapshot can be 500k characters (Amazon's results).
Like Playwright MCP, which keeps snapshots in files instead of returning them whole, the full
snapshot is kept (the trace stores it as a file) and only the parts that matter are read.
The page is cut into parts as it is, unchanged; TypeSafe picks the parts that matter for the
goal and the next action, and the code copies those parts, in page order.
"""

import re
from dataclasses import dataclass

from typesafe_sdk import Choice, TypeSafeBadRequestError

from .usage import MeteredClient

PART_CHARS = 8_000  # target size of one part
PARTS_PER_QUESTION = 2  # parts taken from each question's ranking
LABEL_CHARS = 400  # description of a part; shortened when TypeSafe's window is too small
MIN_LABEL_CHARS = 40
CONTROLS = 4  # controls named first in a description
NAME_CHARS = 40
INTERACTIVE = {"link", "button", "textbox", "searchbox", "combobox", "checkbox", "radio", "option", "tab", "menuitem"}
_NAME = re.compile(r'"((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class View:
    text: str
    chars: int
    parts: int  # parts the page was cut into (1: shown whole)
    shown: list[int]
    probabilities: dict[str, dict[str, float]]


def split_parts(text: str) -> list[str]:
    """Cut the page into parts of about PART_CHARS, at line boundaries."""
    parts: list[list[str]] = [[]]
    size = 0
    for line in text.splitlines():
        if size >= PART_CHARS:
            parts.append([])
            size = 0
        parts[-1].append(line)
        size += len(line) + 1
    return ["\n".join(part) for part in parts]


def describe_part(part: str, chars: int = LABEL_CHARS) -> str:
    """A short description of a part (its controls, then its texts), which TypeSafe reads to pick parts."""
    controls: dict[str, None] = {}
    texts: dict[str, None] = {}
    for line in part.splitlines():
        role = line.lstrip().removeprefix("- ").split(" ", 1)[0].rstrip(":")
        match = _NAME.search(line)
        if match and role in INTERACTIVE:
            controls[f"{role} {match[1][:NAME_CHARS]}"] = None
        elif match:
            texts[match[1][:NAME_CHARS]] = None
        elif ": " in line and not line.rstrip().endswith(":"):
            texts[line.split(": ", 1)[1].strip()[:NAME_CHARS]] = None
    texts.pop("", None)
    # Selects and inputs first: they are what an action operates on, and links are plentiful.
    ordered = sorted(controls, key=lambda c: c.split(" ", 1)[0] in {"link", "option"})
    label = "controls: " + "; ".join(ordered[:CONTROLS]) + " | " + " | ".join(texts)
    return label[:chars]


async def view_page(client: MeteredClient, goal: str, history: list[str], snapshot: str, limit: int) -> View:
    """The part of `snapshot` that TypeSafe should read now."""
    text = snapshot
    if len(text) <= limit:
        return View(text, len(text), 1, [0], {})

    parts = split_parts(text)
    state = {"goal": goal, "history": history or ["(nothing done yet)"]}
    chars = LABEL_CHARS
    while True:
        criteria = {f"part {i}": describe_part(part, chars) for i, part in enumerate(parts)}
        questions = {
            "outcome": Choice(
                instructions=(
                    "The page is too long to read at once and is cut into numbered parts. Which part "
                    "shows the outcome that `goal` asks for, or the progress made toward it, given "
                    "what `history` already did?"
                ),
                criteria=criteria,
            ),
            "control": Choice(
                instructions=(
                    "The page is too long to read at once and is cut into numbered parts. Which part "
                    "contains the control (input, dropdown, button or link) to operate next toward "
                    "`goal`, given what `history` already did?"
                ),
                criteria=criteria,
            ),
        }
        try:
            response = await client.system_one(state, questions)
            break
        except TypeSafeBadRequestError as error:  # too many parts for TypeSafe's window: shorter descriptions
            if "max_tokens_exceeded" not in str(error) or chars <= MIN_LABEL_CHARS:
                raise
            chars //= 2
    chosen: dict[int, None] = {}
    probabilities = {}
    for name in questions:
        answer = response.choices[name]
        probabilities[name] = answer.probabilities
        ranked = sorted(answer.probabilities, key=answer.probabilities.get, reverse=True)
        for part in ranked[:PARTS_PER_QUESTION]:
            chosen[int(part.removeprefix("part "))] = None
    shown = sorted(chosen)

    pieces: list[str] = []
    for i in shown:
        if pieces and i - 1 not in shown:
            pieces.append("... (part of the page omitted) ...")
        pieces.append(parts[i])
    view = View("\n".join(pieces), len(text), len(parts), shown, probabilities)
    client.trace.event(
        "page_view",
        snapshot_chars=len(snapshot),
        parts=len(parts),
        shown=shown,
        probabilities=probabilities,
        labels={i: describe_part(parts[i], chars) for i in shown},
        view_chars=len(view.text),
    )
    return view
