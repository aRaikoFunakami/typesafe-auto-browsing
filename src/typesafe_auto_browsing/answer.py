"""目的が何かを見つけることを求めているときの、答え。

TypeSafe は文章を書けないので、答えは、最終ページの文字列を TypeSafe が選び、コードが写したものになる。
目的が何を求めていても（最安、ポイント最多、最短、事実など）、TypeSafe が目的を読んで判断する:

1. 目的が、何かを見つけることを求めているか。
2. 数量で項目を比べること（最安、ポイント最多、最短: 最小か最大か）を求めているか、何かを読むこと
   （事実や値）を求めているか。
3. ページの各部分で、どの文字列がその数量（または事実）か。
4. その中で、どれが最小・最大か（または事実を述べているか）。
5. 比べる場合は、その数量が属する項目（商品、記事など）の名前を表す文字列はどれか。

結果は 1 つの答えに絞らない。TypeSafe は分布を返すので、答えは有力な候補の一覧になり、それぞれに
確信度（最終の選択での確率）と、その候補がある部分の中で文脈つきで読んだときの確率が付く。
"""

import asyncio
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin

from typesafe_sdk import Choice, Noul

from .arguments import MAX_OPTIONS, NONE, Context, Pick, ask_fitting_page, ask_picks, build_state, goal_candidates
from .page_view import split_parts
from .usage import MeteredClient

MAX_TEXT_CHARS = 400  # 答えとして出す文字列の最大長（商品名は長い）
CONCURRENCY = 8  # 同時に質問する部分の数
WINDOW_BEFORE = 6_000  # 値がどれに属するかを探すために見せる、値より前のページの文字数
WINDOW_AFTER = 4_000
MIN_WANTED = 0.5  # この確率より低いと、目的は質問ではなく操作（「X を検索して」）とみなす
MIN_ANSWER_CONFIDENCE = 0.5  # これより低いと、ページが未完成かもしれない（例: 結果を読み込み中）
ANSWER_ATTEMPTS = 3
SETTLE_SECONDS = 2  # 試行の間の待ち時間
FINALIST_PROBABILITY = 0.2  # 部分の中でこの確率以上の文字列は、決勝に進む
FINALISTS_PER_PART = 3
MAX_CANDIDATES = 5  # 報告する候補の数
MIN_CANDIDATE_CONFIDENCE = 0.05  # これより確率の低い候補は報告しない（最有力の 1 つは必ず報告する）

_NAME = re.compile(r'"((?:[^"\\]|\\.)*)"')


@dataclass
class Subject:
    """ページの文字列と、その確信度（値が属するものを表すのに使う）。"""

    text: str
    confidence: float
    source: str = "page"
    url: str | None = None


@dataclass
class Candidate:
    text: str  # ページの文字列。そのまま写したもの
    confidence: float  # 全部分の候補の中から最終的に選ぶときの確率
    in_part: float  # その候補がある部分の中で、周りの文脈つきで読んだときの確率
    part: int  # その部分の番号
    url: str | None = None  # リンクのときの遷移先
    subject: Subject | None = None  # 比べる目的のとき: 値が属するものの名前


@dataclass
class Answers:
    """`find_answers` が見つけたもの。`wanted` が False: 目的は操作を求めている。候補が空: 報告するものがない。"""

    wanted: bool  # 目的が、報告ではなく操作を求めているとき False
    reason: str
    candidates: list[Candidate] = field(default_factory=list)  # 最有力のものが先頭


def as_dicts(answers: Answers) -> list[dict]:
    """候補を、JSON にできる dict にしたもの。最有力のものが先頭。"""
    return [{"rank": rank, **asdict(candidate)} for rank, candidate in enumerate(answers.candidates, 1)]


def page_texts(page: str) -> list[str]:
    """スナップショットの文字列: 要素の名前とテキスト行。そのまま写す。"""
    found: dict[str, None] = {}
    for line in page.splitlines():
        stripped = line.strip().removeprefix("- ")
        if stripped.startswith("/url:"):
            continue
        if match := _NAME.search(stripped):
            text = match[1].replace('\\"', '"').replace("\\\\", "\\")
        elif ": " in stripped:
            text = stripped.split(": ", 1)[1].strip()
        else:
            continue
        if 0 < len(text) <= MAX_TEXT_CHARS:
            found[text] = None
    return list(found)


def url_of(page: str, text: str, base: str = "") -> str | None:
    """`text` という名前のリンクの遷移先: そのすぐ下の `/url:` 行。"""
    lines = page.splitlines()
    for i, line in enumerate(lines[:-1]):
        if re.search(r"- link ", line) and _NAME.search(line) and _NAME.search(line)[1].replace('\\"', '"') == text:
            nxt = lines[i + 1].strip()
            if nxt.startswith("- /url:"):
                return urljoin(base, nxt.removeprefix("- /url:").strip().strip("\"'"))
    return None


def _page_url(page: str) -> str:
    match = re.search(r"- Page URL: (.*)", page)
    return match[1].strip() if match else ""


def _quantity(hint: str) -> str:
    return f"the quantity named or implied by the words `{hint}` of `goal`" if hint else "the quantity that `goal` compares between items"


def _answer_pick(compare: bool, direction: str, hint: str) -> Pick:
    def question(options: list[str]) -> Choice:
        if compare:
            instructions = (
                f"`page` is one part of a longer page. Which text in it states {_quantity(hint)} of a "
                "whole item listed on the page (for example the price of a product, the points of an "
                "article, the total time of a whole route), and not of a part of an item (a step, a leg, "
                "a fee, a walk)? "
                f"Pick the one whose value is the {direction} in this part. "
                "Pick the last option when this part has no such value."
            )
        else:
            instructions = (
                "`page` is one part of a longer page. Which text in it states what `goal` asks to find "
                "or read (a fact, a value, a name)? Pick the last option when this part has none."
            )
        return Choice(instructions=instructions, criteria={**dict.fromkeys(options), NONE: "This part has none."})

    return Pick(page_texts, question, True)


async def find_answers(client: MeteredClient, goal: str, history: list[str], snapshot: str) -> Answers:
    """ページ `snapshot` にある、`goal` の答え。"""
    response = await client.system_one(
        build_state(goal, history, ""),
        {
            "wanted": Noul(
                instructions=(
                    "The goal in `goal` asks to find and report something on a page (a value, an item, a "
                    "name, a fact), and not only to carry out an operation such as searching or opening."
                )
            )
        },
    )
    wanted = response.nouls["wanted"].noul
    if wanted < MIN_WANTED:
        return Answers(False, f"the goal asks for an operation, not for something to report (p={wanted:.2f})")

    # モジュールの説明の手順 2〜3: 比べるか読むか、何の量で比べるか。
    comparison = (
        await client.system_one(
            build_state(goal, history, ""),
            {
                "compare": Noul(
                    instructions=(
                        "The goal in `goal` asks for the item that has the lowest or the highest value of "
                        "some quantity: the cheapest, the most expensive, the most, the fewest, the "
                        "shortest, the longest, the highest rated, ..."
                    )
                ),
                "direction": Choice(
                    instructions="Does the goal in `goal` ask for the item with the lowest or the highest value?",
                    criteria={
                        "lowest": "The cheapest, the fewest, the shortest, the smallest, the lowest.",
                        "highest": "The most, the longest, the largest, the highest, the best rated.",
                    },
                ),
            },
        )
    )
    compare = comparison.nouls["compare"].noul >= 0.5
    direction = comparison.choices["direction"].choice
    hint = ""
    if compare:  # 目的文のうち、比べる量を指す語（価格、ポイント、所要時間など）
        goal_parts = goal_candidates(goal)

        def hint_question(options: list[str]) -> Choice:
            return Choice(
                instructions=(
                    "Which part of `goal` names the quantity that the items are compared by (for example "
                    "the price, the number of points, the duration), or implies it (cheap implies the "
                    "price)? Pick the last option when no part does."
                ),
                criteria={**dict.fromkeys(options), NONE: "No part names it."},
            )

        context = Context(client, goal, history, "", 0, lambda _line: None)
        _, hint_choices = await ask_picks(context, {}, {"hint": Pick(lambda _t: goal_parts, hint_question, True)})
        choice, hint_confidence = hint_choices.get("hint", (NONE, 0.0))
        hint = choice if choice != NONE and hint_confidence >= 0.5 else ""
    # 手順 3〜4: ページの各部分が、答えらしい文字列を挙げる（並列）。挙がったものどうしで競う。
    parts = split_parts(snapshot)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    value_pick = _answer_pick(compare, direction, hint)

    async def finalists_in_part(index: int) -> list[tuple[int, str, float]]:
        """部分の中で値らしい文字列: 勝者と、僅差のもの。"""
        if not page_texts(parts[index]):
            return []

        def chunks(text: str) -> list[list[str]]:
            options = page_texts(text)
            return [options[i : i + MAX_OPTIONS - 1] for i in range(0, len(options), MAX_OPTIONS - 1)]

        def build(text: str):
            asked = {f"value:{i}": value_pick.question(chunk) for i, chunk in enumerate(chunks(text))}
            return build_state(goal, history, text, next_action="report the answer"), asked

        async with semaphore:
            response, state, _ = await ask_fitting_page(client, parts[index], len(parts[index]), build)
        found: list[tuple[int, str, float]] = []
        for i, _chunk in enumerate(chunks(state["page"])):
            probabilities = response.choices[f"value:{i}"].probabilities
            found += [(index, t, p) for t, p in probabilities.items() if t != NONE and p >= FINALIST_PROBABILITY]
        return sorted(found, key=lambda f: -f[2])[:FINALISTS_PER_PART]

    finalists = [f for found in await asyncio.gather(*(finalists_in_part(i) for i in range(len(parts)))) for f in found]
    if not finalists:
        return Answers(True, "no part of the page has a value the goal asks for")

    # 文字列ごとに、部分の中での最高の確率と、その部分の番号。
    best_in_part: dict[str, tuple[float, int]] = {}
    for index, text, probability in finalists:
        if text not in best_in_part or probability > best_in_part[text][0]:
            best_in_part[text] = (probability, index)
    if len(best_in_part) == 1:
        distribution = {text: probability for text, (probability, _) in best_in_part.items()}
    else:
        distribution = await _final_round(client, goal, history, list(best_in_part), compare, direction, hint)
    ranked = sorted(distribution.items(), key=lambda item: -item[1])
    reported = [item for item in ranked if item[1] >= MIN_CANDIDATE_CONFIDENCE][:MAX_CANDIDATES] or ranked[:1]

    base = _page_url(snapshot)
    candidates = [
        Candidate(text, confidence, best_in_part[text][0], best_in_part[text][1], url_of(snapshot, text, base))
        for text, confidence in reported
    ]
    trace = getattr(client, "trace", None)
    if trace and hint:
        trace.event("answer_quantity", hint=hint, direction=direction)

    if compare:  # 手順 5: 各値が属するもの。ページの中で値の近くにある名前
        subjects = await asyncio.gather(
            *(_find_subject(client, goal, history, snapshot, parts, c, semaphore) for c in candidates)
        )
        for candidate, subject in zip(candidates, subjects):
            candidate.subject = subject
    return Answers(True, "answered", candidates)


async def _find_subject(client, goal, history, snapshot, parts, candidate: Candidate, semaphore) -> Subject | None:
    """`candidate` が属するものの名前。ページ内の周りの文字列から選ぶ。"""
    start = sum(len(part) + 1 for part in parts[: candidate.part])  # スナップショットの中で、その部分が始まる位置
    within = parts[candidate.part]
    line = next((k for k, l in enumerate(within.splitlines()) if candidate.text in l), 0)
    offset = start + len("\n".join(within.splitlines()[:line]))
    window = snapshot[max(0, offset - WINDOW_BEFORE) : offset + WINDOW_AFTER]
    async with semaphore:
        context = Context(client, goal, history, window, len(window), lambda _line: None)
        _, answers = await ask_picks(
            context,
            {},
            {"subject": _subject_pick(candidate.text)},
            next_action={"report": {"value": candidate.text}},
        )
    choice, confidence = answers.get("subject", (NONE, 0.0))
    if choice == NONE:
        return None
    return Subject(choice, confidence, url=url_of(snapshot, choice, _page_url(snapshot)))


async def _final_round(
    client: MeteredClient, goal: str, history: list[str], options: list[str], compare: bool, direction: str, hint: str
) -> dict[str, float]:
    """各候補が、目的に最もよく答える確率（候補が多いときは何回かに分けて聞く）。"""
    pool = list(options)
    while True:
        chunks = [pool[i : i + MAX_OPTIONS - 1] for i in range(0, len(pool), MAX_OPTIONS - 1)]
        distribution: dict[str, float] = {}
        for chunk in chunks:
            if len(chunk) == 1:  # 選ぶ相手がいない
                distribution[chunk[0]] = 1.0
                continue
            # 候補は、それぞれの部分ですでに有力と判断されている。決勝は選ぶだけ。
            question = Choice(
                instructions=(
                    f"`goal` compares whole items by {_quantity(hint)}. Which of these texts has the "
                    f"{direction} value of that quantity?"
                    if compare
                    else "Which of these texts best states what `goal` asks to find or read?"
                ),
                criteria=dict.fromkeys(chunk),
            )
            answer = (await client.system_one(build_state(goal, history, ""), {"best": question})).choices["best"]
            distribution.update({text: p for text, p in answer.probabilities.items() if text in chunk})
        if len(chunks) == 1:
            return distribution
        pool = [max(chunk, key=lambda text: distribution.get(text, 0.0)) for chunk in chunks]  # 各塊の勝者どうしが戦う


def _subject_pick(value_text: str) -> Pick:
    def options(text: str) -> list[str]:
        return [t for t in page_texts(text) if t != value_text]

    def question(candidates: list[str]) -> Choice:
        return Choice(
            instructions=(
                f"Which text names the thing (a product, an article, a person, a place, ...) that the "
                f"value `{value_text}` belongs to: its title or name in `page`? Pick the last option "
                f"when the value is itself the answer, as a fact is, or when no such name is in `page`."
            ),
            criteria={**dict.fromkeys(candidates), NONE: "There is no such name."},
        )

    return Pick(options, question, True)
