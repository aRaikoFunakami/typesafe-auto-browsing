"""ページのうち、TypeSafe が見るもの。

TypeSafe が読めるのは最大でおよそ 32k トークンだが、スナップショットは 50 万文字になりうる
（Amazon の検索結果）。Playwright MCP がスナップショットを丸ごと返さずファイルに置くのと同じように、
全文は保存しておき（トレースがファイルとして残す）、必要な部分だけを読む。
ページはそのまま部分に切る。TypeSafe が各部分を（並列で）読み、目的の成果か次に操作する部品が
その部分にあるかを答える。コードは、あると答えられた部分を、ページの順に写す。
"""

import asyncio
from dataclasses import dataclass

from typesafe_sdk import Noul

from .arguments import ask_fitting_page, build_state
from .usage import MeteredClient

PART_CHARS = 8_000  # 1 つの部分の目標の大きさ
CONCURRENCY = 8  # 同時に質問する部分の数
SHOW_PROBABILITY = 0.2  # この確率に満たない部分は見せない（最有力の 1 つは必ず見せる）

_PART_NOTE = "`page` is one part of a longer page. "
HOLDS_OUTCOME = Noul(
    instructions=(
        _PART_NOTE + "Does it show the outcome that `goal` asks for, or the progress made toward it, "
        "given what `history` already did?"
    ),
    criteria={
        "true": "This part shows the outcome or the progress made toward it.",
        "false": "This part shows neither.",
    },
)
HOLDS_CONTROL = Noul(
    instructions=(
        _PART_NOTE + "Does it contain the control (input, dropdown, button or link) to operate next "
        "toward `goal`, given what `history` already did?"
    ),
    criteria={
        "true": "The control to operate next is in this part.",
        "false": "The control to operate next is not in this part.",
    },
)


@dataclass(frozen=True)
class View:
    text: str
    chars: int
    parts: int  # ページを切った部分の数（1: 全体をそのまま見せた）
    shown: list[int]
    probabilities: dict[str, dict[str, float]]  # 質問ごとの、各部分の確率


def split_parts(text: str) -> list[str]:
    """ページを、行の区切りで、PART_CHARS ほどの部分に切る。"""
    parts: list[list[str]] = [[]]
    size = 0
    for line in text.splitlines():
        if size >= PART_CHARS:
            parts.append([])
            size = 0
        parts[-1].append(line)
        size += len(line) + 1
    return ["\n".join(part) for part in parts]


async def view_page(client: MeteredClient, goal: str, history: list[str], snapshot: str, limit: int) -> View:
    """`snapshot` のうち、今 TypeSafe が読むべき部分（最大 `limit` 文字）。"""
    text = snapshot
    if len(text) <= limit:
        return View(text, len(text), 1, [0], {})

    parts = split_parts(text)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def ask_part(part: str) -> tuple[float, float]:
        async with semaphore:
            response, _, _ = await ask_fitting_page(
                client,
                part,
                len(part),
                lambda t: (build_state(goal, history, t), {"outcome": HOLDS_OUTCOME, "control": HOLDS_CONTROL}),
            )
        return response.nouls["outcome"].noul, response.nouls["control"].noul

    asked = await asyncio.gather(*(ask_part(part) for part in parts))
    likelihood = [max(pair) for pair in asked]

    # 確率の高い部分から、収まる限り見せる。最有力の 1 つは必ず見せる。
    shown: list[int] = []
    size = 0
    for i in sorted(range(len(parts)), key=lambda i: -likelihood[i]):
        if (shown and likelihood[i] < SHOW_PROBABILITY) or (shown and size + len(parts[i]) > limit):
            break
        shown.append(i)
        size += len(parts[i])
    shown.sort()

    pieces: list[str] = []
    for i in shown:
        if pieces and i - 1 not in shown:
            pieces.append("... (part of the page omitted) ...")
        pieces.append(parts[i])
    probabilities = {
        "outcome": {f"part {i}": pair[0] for i, pair in enumerate(asked)},
        "control": {f"part {i}": pair[1] for i, pair in enumerate(asked)},
    }
    view = View("\n".join(pieces), len(text), len(parts), shown, probabilities)
    client.trace.event(
        "page_view",
        snapshot_chars=len(snapshot),
        parts=len(parts),
        shown=shown,
        probabilities=probabilities,
        view_chars=len(view.text),
    )
    return view
