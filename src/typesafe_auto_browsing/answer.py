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

from .arguments import MAX_OPTIONS, NONE, Context, Pick, ask_fitting_page, ask_picks, build_state, goal_candidates
from .page_view import split_parts
from .usage import MeteredClient

MAX_TEXT_CHARS = 400  # longest text offered as an answer (product titles are long)
CONCURRENCY = 8  # parts asked at the same time
WINDOW_BEFORE = 6_000  # chars of the page before the value, shown to find what the value belongs to
WINDOW_AFTER = 4_000
MIN_WANTED = 0.5  # below this probability the goal is an operation ("search for X"), not a question
MIN_ANSWER_CONFIDENCE = 0.5  # below this the page may not be ready (e.g. results still loading)
ANSWER_ATTEMPTS = 3
SETTLE_SECONDS = 2  # wait between attempts
FINALIST_PROBABILITY = 0.2  # a text of a part that is at least this likely goes to the final round
FINALISTS_PER_PART = 3
MAX_CANDIDATES = 5  # candidates reported
MIN_CANDIDATE_CONFIDENCE = 0.05  # a candidate less likely than this is not reported (the best one always is)

_NAME = re.compile(r'"((?:[^"\\]|\\.)*)"')


@dataclass
class Subject:
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
    subject: Subject | None = None  # for a comparison: the name of what the value belongs to


@dataclass
class Answers:
    """What `find_answers` found. `wanted` False: the goal asks for an operation. No candidates: nothing to report."""

    wanted: bool  # False when the goal asks for an operation, not for something to report
    reason: str
    candidates: list[Candidate] = field(default_factory=list)  # most likely first


def as_dicts(answers: Answers) -> list[dict]:
    """The candidates as JSON-ready dicts, most likely first."""
    return [{"rank": rank, **asdict(candidate)} for rank, candidate in enumerate(answers.candidates, 1)]


def page_texts(page: str) -> list[str]:
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
    """The answer to `goal` on the page `snapshot`."""
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

    # Steps 2-3 of the module docstring: compare or read, and by which quantity.
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
        _, hint_choices = await ask_picks(context, {}, {"hint": Pick(lambda _t: goal_parts, hint_question, True)})
        choice, hint_confidence = hint_choices.get("hint", (NONE, 0.0))
        hint = choice if choice != NONE and hint_confidence >= 0.5 else ""
    # Steps 3-4: each part of the page nominates its likely texts (in parallel); the nominees then compete.
    parts = split_parts(snapshot)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    value_pick = _answer_pick(compare, direction, hint)

    async def finalists_in_part(index: int) -> list[tuple[int, str, float]]:
        """The texts of a part that are likely the value: the winner and the close ones."""
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

    # Per text: its best probability in a part, and which part.
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

    if compare:  # step 5: what each value belongs to: a name near it in the page
        subjects = await asyncio.gather(
            *(_find_subject(client, goal, history, snapshot, parts, c, semaphore) for c in candidates)
        )
        for candidate, subject in zip(candidates, subjects):
            candidate.subject = subject
    return Answers(True, "answered", candidates)


async def _find_subject(client, goal, history, snapshot, parts, candidate: Candidate, semaphore) -> Subject | None:
    """The name of what `candidate` belongs to, chosen among the texts around it in the page."""
    start = sum(len(part) + 1 for part in parts[: candidate.part])  # where its part starts in the snapshot
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
            answer = (await client.system_one(build_state(goal, history, ""), {"best": question})).choices["best"]
            distribution.update({text: p for text, p in answer.probabilities.items() if text in chunk})
        if len(chunks) == 1:
            return distribution
        pool = [max(chunk, key=lambda text: distribution.get(text, 0.0)) for chunk in chunks]  # the winners meet


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
