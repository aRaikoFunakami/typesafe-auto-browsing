"""The answer to a goal that asks to find something.

TypeSafe cannot write text, so an answer is a text of the final page that TypeSafe chooses and the code
copies. Whatever the goal asks for (the cheapest, the one with the most points, the shortest, a fact),
TypeSafe reads the goal and decides:

1. whether the goal asks for something to be found at all;
2. whether it asks to compare items by a quantity (the cheapest, the most points, the shortest: the lowest or
   the highest) or to read something (a fact, a value);
3. in each part of the page, which text is that quantity (or that fact) in that part;
4. among those, which is the lowest / the highest (or which states the fact);
5. for a comparison, which text names the item that quantity belongs to (a product, an article, ...).

The result is not forced into one answer: TypeSafe gives a distribution, so the answer is the likely
candidates, each with its confidence (its probability in the final choice) and the probability it had in
its own part of the page, where it was read with its context.
"""

import asyncio
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin

from typesafe_sdk import Choice, Noul

from .arguments import MAX_OPTIONS, NONE, Context, Pick, _ask, fit, goal_candidates, state_of
from .page_view import _parts
from .usage import MeteredClient

MAX_TEXT_CHARS = 400  # longest text offered as an answer (product titles are long)
CONCURRENCY = 8  # parts asked at the same time
WINDOW_BEFORE = 6_000  # chars of the page before the value, shown to find what the value belongs to
WINDOW_AFTER = 4_000
MIN_WANTED = 0.5
MIN_ANSWER_CONFIDENCE = 0.5  # below this the page may not be ready (e.g. results still loading)
ANSWER_ATTEMPTS = 3
SETTLE_SECONDS = 2  # wait between attempts
FINALIST_PROBABILITY = 0.2  # a text of a part that is at least this likely goes to the final round
FINALISTS_PER_PART = 3
MAX_CANDIDATES = 5  # candidates reported
MIN_CANDIDATE_CONFIDENCE = 0.05  # a candidate less likely than this is not reported (the best one always is)

_NAME = re.compile(r'"((?:[^"\\]|\\.)*)"')


@dataclass
class Answer:
    """A text of the page with its confidence (used for what a value belongs to)."""

    text: str
    confidence: float
    source: str = "page"
    url: str | None = None


@dataclass
class Candidate:
    text: str  # a text of the page, copied as it is
    confidence: float  # probability in the final choice among the candidates of all parts
    in_part: float  # probability in its own part of the page, where it was read with its context
    part: int  # index of that part
    url: str | None = None  # where it links to, when it is a link
    subject: Answer | None = None  # for a comparison: the name of what the value belongs to


@dataclass
class Answers:
    asked: bool
    reason: str
    candidates: list[Candidate] = field(default_factory=list)  # most likely first


def as_dicts(answers: Answers) -> list[dict]:
    """The candidates as JSON-ready dicts, most likely first."""
    return [{"rank": rank, **asdict(candidate)} for rank, candidate in enumerate(answers.candidates, 1)]


def texts(page: str) -> list[str]:
    """The texts of a snapshot: the names of its elements and the text lines, copied as they are."""
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
    """The link target of the link named `text`: the `/url:` line right under it."""
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


def _value_pick(compare: bool, direction: str, hint: str) -> Pick:
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

    return Pick(texts, question, True)


async def find_answers(client: MeteredClient, goal: str, history: list[str], snapshot: str) -> Answers:
    """The answer to `goal` on the page `snapshot`."""
    response = await client.system_one(
        state_of(goal, history, ""),
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

    kind = (
        await client.system_one(
            state_of(goal, history, ""),
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
    compare = kind.nouls["compare"].noul >= 0.5
    direction = kind.choices["direction"].choice
    hint = ""
    if compare:  # the words of the goal that name the quantity compared (a price, points, a duration)
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
        _, chosen = await _ask(context, {}, {"hint": Pick(lambda _t: goal_parts, hint_question, True)})
        choice, hint_confidence = chosen.get("hint", (NONE, 0.0))
        hint = choice if choice != NONE and hint_confidence >= 0.5 else ""
    parts = _parts(snapshot)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    value_pick = _value_pick(compare, direction, hint)

    async def in_part(index: int) -> list[tuple[int, str, float]]:
        """The texts of a part that are likely the value: the winner and the close ones."""
        if not texts(parts[index]):
            return []

        def chunks(text: str) -> list[list[str]]:
            options = texts(text)
            return [options[i : i + MAX_OPTIONS - 1] for i in range(0, len(options), MAX_OPTIONS - 1)]

        def build(text: str):
            asked = {f"value:{i}": value_pick.question(chunk) for i, chunk in enumerate(chunks(text))}
            return state_of(goal, history, text, next_action="report the answer"), asked

        async with semaphore:
            response, state, _ = await fit(client, parts[index], len(parts[index]), build)
        found: list[tuple[int, str, float]] = []
        for i, _chunk in enumerate(chunks(state["page"])):
            probabilities = response.choices[f"value:{i}"].probabilities
            found += [(index, t, p) for t, p in probabilities.items() if t != NONE and p >= FINALIST_PROBABILITY]
        return sorted(found, key=lambda f: -f[2])[:FINALISTS_PER_PART]

    winners = [w for found in await asyncio.gather(*(in_part(i) for i in range(len(parts)))) for w in found]
    if not winners:
        return Answers(True, "no part of the page has a value the goal asks for")

    # Per text: its best probability in a part, and which part.
    in_parts: dict[str, tuple[float, int]] = {}
    for index, text, probability in winners:
        if text not in in_parts or probability > in_parts[text][0]:
            in_parts[text] = (probability, index)
    if len(in_parts) == 1:
        distribution = {text: probability for text, (probability, _) in in_parts.items()}
    else:
        distribution = await _final(client, goal, history, list(in_parts), compare, direction, hint)
    ranked = sorted(distribution.items(), key=lambda item: -item[1])
    chosen = [item for item in ranked if item[1] >= MIN_CANDIDATE_CONFIDENCE][:MAX_CANDIDATES] or ranked[:1]

    base = _page_url(snapshot)
    candidates = [
        Candidate(text, confidence, in_parts[text][0], in_parts[text][1], url_of(snapshot, text, base))
        for text, confidence in chosen
    ]
    trace = getattr(client, "trace", None)
    if trace and hint:
        trace.event("answer_quantity", hint=hint, direction=direction)

    if compare:  # what each value belongs to: a name near it in the page
        subjects = await asyncio.gather(
            *(_subject(client, goal, history, snapshot, parts, c, semaphore) for c in candidates)
        )
        for candidate, subject in zip(candidates, subjects):
            candidate.subject = subject
    return Answers(True, "answered", candidates)


async def _subject(client, goal, history, snapshot, parts, candidate: Candidate, semaphore) -> Answer | None:
    """The name of what `candidate` belongs to, chosen among the texts around it in the page."""
    start = sum(len(part) + 1 for part in parts[: candidate.part])  # where its part starts in the snapshot
    within = parts[candidate.part]
    line = next((k for k, l in enumerate(within.splitlines()) if candidate.text in l), 0)
    offset = start + len("\n".join(within.splitlines()[:line]))
    window = snapshot[max(0, offset - WINDOW_BEFORE) : offset + WINDOW_AFTER]
    async with semaphore:
        context = Context(client, goal, history, window, len(window), lambda _line: None)
        _, answers = await _ask(
            context,
            {},
            {"subject": _subject_pick(candidate.text)},
            next_action={"report": {"value": candidate.text}},
        )
    choice, confidence = answers.get("subject", (NONE, 0.0))
    if choice == NONE:
        return None
    return Answer(choice, confidence, url=url_of(snapshot, choice, _page_url(snapshot)))


async def _final(
    client: MeteredClient, goal: str, history: list[str], options: list[str], compare: bool, direction: str, hint: str
) -> dict[str, float]:
    """The probability of each candidate to best answer the goal (asked in rounds when there are many)."""
    pool = list(options)
    while True:
        chunks = [pool[i : i + MAX_OPTIONS - 1] for i in range(0, len(pool), MAX_OPTIONS - 1)]
        distribution: dict[str, float] = {}
        for chunk in chunks:
            if len(chunk) == 1:  # nothing to choose between
                distribution[chunk[0]] = 1.0
                continue
            # The candidates were already found likely in their parts, so the final round only chooses.
            question = Choice(
                instructions=(
                    f"`goal` compares whole items by {_quantity(hint)}. Which of these texts has the "
                    f"{direction} value of that quantity?"
                    if compare
                    else "Which of these texts best states what `goal` asks to find or read?"
                ),
                criteria=dict.fromkeys(chunk),
            )
            answer = (await client.system_one(state_of(goal, history, ""), {"best": question})).choices["best"]
            distribution.update({text: p for text, p in answer.probabilities.items() if text in chunk})
        if len(chunks) == 1:
            return distribution
        pool = [max(chunk, key=lambda text: distribution.get(text, 0.0)) for chunk in chunks]  # the winners meet


def _subject_pick(value_text: str) -> Pick:
    def options(text: str) -> list[str]:
        return [t for t in texts(text) if t != value_text]

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
