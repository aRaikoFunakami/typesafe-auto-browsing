"""Deciding the arguments of a tool call.

The tool's `input_schema` is the only source of what an argument is. TypeSafe chooses every value
(element refs, enum values, booleans, options, and free-form strings and numbers, which are chosen
among candidates taken from the goal and the page); the code copies the chosen candidate. Nothing is
generated. When an argument has no candidate the tool cannot be used at that step, and the caller
chooses another tool.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from mcp.types import Tool
from typesafe_sdk import Choice, Noul, TypeSafeBadRequestError

from .keys import KEYS
from .usage import MeteredClient

MAX_OPTIONS = 255  # options per Choice question
MIN_PAGE_CHARS = 2_000
MIN_CONFIDENCE = 0.5  # below this a candidate is too doubtful to be used as a free-form value
MAX_NAME_CHARS = 100  # longest page text offered as a value
MAX_ENTRIES = 10  # entries of an array of objects (e.g. the fields of browser_fill_form) in one call
NONE = "(none of the above)"
# Never decided: `element` is only a human-readable label, `filename` would move the output out of the
# response, and `depth` would cut the snapshot tree.
OMITTED = {"element", "filename", "depth"}
NAMED_VALUES = {("browser_press_key", "key"): KEYS}
PAGE_SUFFIX = "@page"  # the pick among the texts of the page, asked next to the pick among the goal

_REF = re.compile(r"\[ref=(\w+)\]")
_NAME = re.compile(r'"((?:[^"\\]|\\.)*)"')
_OPTION = re.compile(r'option "(.*?)"(?: \[[^\]]*\])*:?$')
_INDEX = re.compile(r"^\s*(?:- )?\[?(\d+)\]?[:.]\s", re.MULTILINE)
_EITHER = re.compile(r"either `?(\w+)`? or `?(\w+)`?,? not both", re.IGNORECASE)
_MODAL = re.compile(r"### Modal state\n(.*?)(?:\n###|\Z)", re.DOTALL)
_HANDLER = re.compile(r"can be handled by (\w+)")

Log = Callable[[str], None]


@dataclass
class Context:
    client: MeteredClient
    goal: str
    history: list[str]
    page: str  # what TypeSafe is shown of the page
    limit: int  # longest part of `page` sent
    log: Log
    extra: dict = field(default_factory=dict)  # more state, e.g. the output of the last read-only tool
    last_output: str = ""  # complete output of the last tool, for numbers it lists
    failed_refs: frozenset[str] = frozenset()  # refs this tool already failed on, on this page


@dataclass
class Decision:
    arguments: dict
    sources: dict[str, str]  # argument -> where its value comes from: goal, page, list, schema
    unusable: str | None = None


def modal_handlers(text: str) -> list[str]:
    """Tools that can handle the modal state (dialog, file chooser) shown in a Playwright MCP output."""
    match = _MODAL.search(text)
    return list(dict.fromkeys(_HANDLER.findall(match[1]))) if match else []


# --- schema -----------------------------------------------------------------------------------


def _enum(spec: dict) -> list[str] | None:
    if spec.get("enum"):
        return list(spec["enum"])
    return next((list(alt["enum"]) for alt in spec.get("anyOf", []) if alt.get("enum")), None)


def _type(spec: dict) -> str | None:
    if spec.get("type"):
        return spec["type"]
    return next((alt.get("type") for alt in spec.get("anyOf", []) if alt.get("type") != "null"), None)


def exclusive_pairs(properties: dict) -> list[tuple[str, str]]:
    """Pairs of arguments that the schema says not to give together ("Provide either text or regex, not both")."""
    pairs = []
    for spec in properties.values():
        for first, second in _EITHER.findall(spec.get("description", "")):
            if first in properties and second in properties and (first, second) not in pairs:
                pairs.append((first, second))
    return pairs


def is_ref(name: str) -> bool:
    return name in ("target", "ref") or name.endswith("Target")


def _needs_code(spec: dict) -> bool:
    description = spec.get("description", "").lower()
    return "javascript" in description or "/* code */" in description


def _has_nested_ref(spec: object) -> bool:
    if not isinstance(spec, dict):
        return False
    properties = spec.get("properties", {})
    return any(is_ref(name) for name in properties) or any(
        _has_nested_ref(nested) for nested in (*properties.values(), spec.get("items"))
    )


def _flat_object_reason(schema: dict) -> str | None:
    """Why an object (an entry of an array argument) cannot be filled one value at a time."""
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        spec = properties.get(name, {})
        kind = _type(spec)
        if kind in ("object", "array"):
            return f"`{name}` is nested more than one level deep"
        if kind == "string" and not _enum(spec) and _needs_code(spec):
            return f"`{name}` is code, which TypeSafe cannot write"
    return None


def unusable_reason(tool: Tool) -> str | None:
    """Why the schema alone makes the tool impossible to fill by choosing; None when it is possible."""
    schema = tool.input_schema
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        spec = properties.get(name, {})
        kind = _type(spec)
        if kind == "object":
            return f"`{name}` is a nested structure that cannot be chosen one value at a time"
        if kind == "array" and _type(spec.get("items", {})) == "object":
            if reason := _flat_object_reason(spec["items"]):
                return f"`{name}`: {reason}"
        if kind == "string" and not _enum(spec) and _needs_code(spec):
            return f"`{name}` is code, which TypeSafe cannot write"
    return None


# --- candidates -------------------------------------------------------------------------------


def _char_class(char: str) -> str:
    code = ord(char)
    if code < 128:
        return "latin"  # words, URLs, paths: everything up to the next space or Japanese character
    if 0x3040 <= code <= 0x309F:
        return "hiragana"
    if 0x30A0 <= code <= 0x30FF:
        return "katakana"
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
        return "kanji"
    return "other"


def goal_candidates(goal: str) -> list[str]:
    """Parts of the goal that can be a value: runs of one kind of character (a word, a URL, a kanji
    word, ...) and every run of consecutive such parts, copied exactly as written."""
    tokens: list[tuple[int, int]] = []
    for index, char in enumerate(goal):
        if char.isspace():
            continue
        kind = _char_class(char)
        if tokens and tokens[-1][1] == index and _char_class(goal[tokens[-1][1] - 1]) == kind:
            tokens[-1] = (tokens[-1][0], index + 1)
        else:
            tokens.append((index, index + 1))
    spans = {goal[tokens[i][0] : tokens[j][1]] for i in range(len(tokens)) for j in range(i, len(tokens))}
    spans.update(goal.split())
    return sorted(spans, key=lambda s: (goal.index(s), len(s)))


def page_names(page: str) -> list[str]:
    """Names of the elements in the snapshot: the text of links, buttons, headings, ..."""
    names: dict[str, None] = {}
    for line in page.splitlines():
        if match := _NAME.search(line):
            name = match[1].replace('\\"', '"').replace("\\\\", "\\")
            if 0 < len(name) <= MAX_NAME_CHARS:
                names[name] = None
    return list(names)


def element_name(page: str, ref: str) -> str:
    """The name of the element `ref` in the snapshot (its role when it has none)."""
    line = next((l for l in page.splitlines() if f"[ref={ref}]" in l), "")
    if match := _NAME.search(line):
        return match[1].replace('\\"', '"').replace("\\\\", "\\")
    return line.strip().removeprefix("- ").split(" ", 1)[0].rstrip(":") or ref


def _numbers(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\d+(?:\.\d+)?", text)))


def _refs(page: str, failed: frozenset[str]) -> list[str]:
    return [ref for ref in dict.fromkeys(_REF.findall(page)) if ref not in failed]


def options_under(page: str, ref: str) -> list[str]:
    """Labels of the `option` lines nested under the element `ref` in the snapshot."""
    lines = page.splitlines()
    for i, line in enumerate(lines):
        if f"[ref={ref}]" not in line:
            continue
        indent = len(line) - len(line.lstrip())
        labels = []
        for nested in lines[i + 1 :]:
            if len(nested) - len(nested.lstrip()) <= indent:
                break
            text = nested.strip().removeprefix("- ")
            if text.startswith("'") and text.endswith("'"):  # YAML quotes text that contains ": "
                text = text[1:-1].replace("''", "'")
            if match := _OPTION.match(text):
                labels.append(match[1])
        return list(dict.fromkeys(labels))
    return []


# --- asking TypeSafe --------------------------------------------------------------------------


async def fit(client: MeteredClient, page: str, limit: int, build: Callable):
    """system_one over `build(page[:limit]) -> (state, questions)`.

    TypeSafe's context is limited (32k tokens), so the page is halved until it fits.
    Returns the response, the state that was accepted and the page length used.
    """
    while True:
        state, questions = build(page[:limit])
        try:
            return await client.system_one(state, questions), state, limit
        except TypeSafeBadRequestError as error:
            if "max_tokens_exceeded" not in str(error) or limit <= MIN_PAGE_CHARS:
                raise
            limit //= 2


def state_of(goal: str, history: list[str], page: str, **extra: object) -> dict:
    return {"goal": goal, "history": history or ["(nothing done yet)"], "page": page, **extra}


@dataclass
class Pick:
    """A question with many options: TypeSafe picks one, or NONE."""

    options: Callable[[str], list[str]]  # options offered for the page text that is sent
    question: Callable[[list[str]], Choice]  # the question over a list of at most MAX_OPTIONS options
    optional: bool


def _chunks(options: list[str]) -> list[list[str]]:
    size = MAX_OPTIONS - 1  # leave room for NONE
    return [options[i : i + size] for i in range(0, len(options), size)]


def _criteria(options: list[str], notes: dict[str, str] | None = None) -> dict[str, str | None]:
    criteria: dict[str, str | None] = {option: (notes or {}).get(option) for option in options}
    criteria[NONE] = "No option above is the value."
    return criteria


async def _ask(ctx: Context, questions: dict, picks: dict[str, Pick], **state_extra):
    """One request for the plain questions and every pick. Picks with more options than one question
    can hold are asked in chunks, and the chunk winners meet in a second request.

    Returns the response and, per pick, (chosen option or NONE, confidence)."""

    def chunks_for(text: str) -> dict[str, list[list[str]]]:
        return {name: _chunks(pick.options(text)) for name, pick in picks.items()}

    def alone(name: str, chunk: list[str]) -> bool:
        return not picks[name].optional and len(chunk) == 1  # nothing to choose between

    def build(text: str):
        asked = dict(questions)
        for name, chunks in chunks_for(text).items():
            for i, chunk in enumerate(chunks):
                if not alone(name, chunk):
                    asked[f"{name}:{i}"] = picks[name].question(chunk)
        return state_of(ctx.goal, ctx.history, text, **ctx.extra, **state_extra), asked

    response, state, _ = await fit(ctx.client, ctx.page, ctx.limit, build)
    text = state["page"]
    answers: dict[str, tuple[str, float]] = {}
    finalists: dict[str, list[str]] = {}
    for name, chunks in chunks_for(text).items():
        winners = [
            (chunk[0], 1.0) if alone(name, chunk) else (response.choices[f"{name}:{i}"].choice, response.choices[f"{name}:{i}"].confidence)
            for i, chunk in enumerate(chunks)
        ]
        if len(winners) == 1:
            answers[name] = winners[0]
        elif winners:  # too many options for one question: choose among each chunk's winner
            names = list(dict.fromkeys(choice for choice, _ in winners if choice != NONE))
            if len(names) > 1:
                finalists[name] = names
            else:
                answers[name] = (names[0] if names else NONE, max(confidence for _, confidence in winners))
    if finalists:
        final, _, _ = await fit(
            ctx.client,
            text,
            len(text),
            lambda t: (
                state_of(ctx.goal, ctx.history, t, **ctx.extra, **state_extra),
                {name: picks[name].question(names) for name, names in finalists.items()},
            ),
        )
        for name in finalists:
            answers[name] = (final.choices[name].choice, final.choices[name].confidence)
    return response, answers


def _ref_pick(tool: Tool, name: str, detail: str, ctx: Context, optional: bool) -> Pick:
    def question(refs: list[str]) -> Choice:
        return Choice(
            instructions=(
                f"Which element of the current `page`, identified by its [ref], should the next "
                f"`{tool.name}` call use as `{name}`, given `goal` and what `history` already did? {detail}"
            ),
            criteria=_criteria(refs) if optional else dict.fromkeys(refs),
        )

    return Pick(lambda text: _refs(text, ctx.failed_refs), question, optional)


def _value_pick(tool: Tool, name: str, detail: str, candidates: Callable[[str], list[str]], notes: dict) -> Pick:
    def question(options: list[str]) -> Choice:
        return Choice(
            instructions=(
                f"What should `{name}` be for the next `{tool.name}` call? {detail} The candidates are "
                f"parts of `goal` or texts of `page`, and some contain the value together with words "
                f"around it. Pick the shortest candidate that is completely the value, without the words "
                f"that only give instructions (such as which site to use or what to do), given what "
                f"`history` already did. Prefer a part of `goal` to text of `page`. Pick the last option "
                f"when no candidate is the value."
            ),
            criteria=_criteria(options, notes),
        )

    return Pick(candidates, question, True)


async def decide(ctx: Context, tool: Tool) -> Decision:
    """Decide the arguments of `tool` for the current page."""
    return await _decide_object(ctx, tool, tool.input_schema, {})


async def _decide_object(ctx: Context, tool: Tool, schema: dict, outer: dict, item: bool = False) -> Decision:
    """Decide the values of an object described by `schema`: the arguments of the tool, or one entry of
    an array argument. `outer` is what is already decided around it, shown to TypeSafe as the action."""
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    head = {"tool": tool.name, **outer}
    goal_parts = goal_candidates(ctx.goal)
    goal_numbers = _numbers(ctx.goal)
    # Numbers a tool's output lists: tabs (`- 0: (current) ...`), requests (`1. [GET] ...`).
    listed = _INDEX.findall(ctx.last_output) + _INDEX.findall(ctx.page[:3_000])
    objects: list[str] = []  # arrays of objects: entries chosen one by one
    name_of_element = False  # an entry's human-readable `name`: the name of its element

    arguments: dict[str, object] = {}
    sources: dict[str, str] = {}
    questions: dict[str, Choice | Noul] = {}
    picks: dict[str, Pick] = {}
    later: list[str] = []  # array arguments: chosen after the element they belong to
    kinds: dict[str, str] = {}

    for name, spec in properties.items():
        if name in OMITTED:
            continue
        detail = spec.get("description", "")
        kind = _type(spec)
        enum = _enum(spec)
        optional = name not in required
        if is_ref(name):
            picks[name] = _ref_pick(tool, name, detail, ctx, optional)
            kinds[name] = "ref"
        elif enum:
            options = [*enum, NONE] if optional and "default" not in spec else enum
            questions[name] = Choice(
                instructions=f"Which value of `{name}` should the next `{tool.name}` call use? {detail}",
                criteria={o: ("Leave it unspecified." if o == NONE else None) for o in options},
            )
            kinds[name] = "enum"
        elif kind == "boolean":
            questions[name] = Noul(
                instructions=f"For the next `{tool.name}` call, `{name}` should be true. ({detail})"
            )
            kinds[name] = "boolean"
        elif kind == "array" and _enum(spec.get("items", {})):
            for item in _enum(spec["items"]):
                questions[f"{name}.{item}"] = Noul(
                    instructions=f"For the next `{tool.name}` call, `{name}` should include `{item}`. ({detail})"
                )
            kinds[name] = "flags"
        elif kind == "array" and _type(spec.get("items", {})) == "string":
            later.append(name)
        elif kind == "array" and _type(spec.get("items", {})) == "object":
            if optional:
                continue
            objects.append(name)
        elif kind in ("integer", "number"):
            numbers = list(dict.fromkeys([*goal_numbers, *(listed if name == "index" else [])]))
            notes = {n: "listed in the last output" for n in listed if n not in goal_numbers}
            picks[name] = _value_pick(tool, name, detail, lambda _t, n=numbers: n, notes)
            kinds[name] = kind
        elif kind == "string" and _needs_code(spec):
            continue  # code cannot be chosen
        elif kind == "string" and item and name == "name" and any(is_ref(n) for n in properties):
            name_of_element = True
        elif kind == "string":
            # The goal first: its few parts are asked apart from the many texts of the page, and a text of
            # the page is used only when no part of the goal is the value.
            named = [*NAMED_VALUES.get((tool.name, name), ()), *re.findall(r"`(true|false)`", detail)]
            from_goal = list(dict.fromkeys([*named, *goal_parts]))
            picks[name] = _value_pick(tool, name, detail, lambda _t, c=from_goal: c, {})
            picks[f"{name}{PAGE_SUFFIX}"] = _value_pick(
                tool, name, detail, lambda t, g=from_goal: [n for n in page_names(t) if n not in g], {}
            )
            kinds[name] = kinds[f"{name}{PAGE_SUFFIX}"] = "string"
        elif optional:
            continue  # an optional structure: left out
        else:
            return Decision({}, {}, f"`{name}` has a type that cannot be chosen ({kind})")

    if not properties:
        return Decision({}, {}, None)

    def apply(answers: dict[str, tuple[str, float]]) -> None:
        ordered = sorted(answers, key=lambda n: n.endswith(PAGE_SUFFIX))  # goal picks first
        for pick_name in ordered:
            choice, confidence = answers[pick_name]
            name = pick_name.removesuffix(PAGE_SUFFIX)
            if name in arguments:  # already decided from the goal
                continue
            if choice == NONE:
                ctx.log(f"    typesafe: {pick_name} has no fitting value")
                continue
            if kinds[pick_name] != "ref" and confidence < MIN_CONFIDENCE:
                ctx.log(f"    typesafe: {pick_name} = {choice!r} is unsure (confidence {confidence:.2f})")
                continue
            if kinds[name] in ("integer", "number"):
                number = float(choice)
                arguments[name] = int(number) if kinds[name] == "integer" or number.is_integer() else number
                sources[name] = "goal" if choice in goal_numbers else "list"
            elif kinds[name] == "string":
                arguments[name] = choice
                sources[name] = "page" if pick_name.endswith(PAGE_SUFFIX) else "goal"
            else:
                arguments[name] = choice
                sources[name] = "page"
            ctx.log(f"    typesafe: {name} = {choice!r} (confidence {confidence:.2f})")

    # First the choices and the elements; then the values, which TypeSafe decides knowing which element
    # the action is about (the text to type depends on the input it goes into).
    ref_picks = {n: p for n, p in picks.items() if kinds[n] == "ref"}
    value_picks = {n: p for n, p in picks.items() if kinds[n] != "ref"}
    if questions or ref_picks:
        response, answers = await _ask(ctx, questions, ref_picks, next_action=head if outer else tool.name)
        for name, question in questions.items():
            if isinstance(question, Choice):
                answer = response.choices[name]
                if answer.choice != NONE:
                    arguments[name], sources[name] = answer.choice, "schema"
                ctx.log(f"    typesafe: {name} = {answer.choice!r} (confidence {answer.confidence:.2f})")
            else:
                probability = response.nouls[name].noul
                base = name.split(".")[0]
                if kinds.get(base) == "boolean" and (base in required or probability >= 0.5):
                    arguments[name], sources[name] = probability >= 0.5, "schema"
                    ctx.log(f"    typesafe: {name} = {probability >= 0.5} (p={probability:.2f})")
                elif "." in name and probability >= 0.5:
                    arguments.setdefault(base, []).append(name.split(".", 1)[1])
                    sources[base] = "schema"
                    ctx.log(f"    typesafe: {base} += {name.split('.', 1)[1]} (p={probability:.2f})")
        apply(answers)
    if name_of_element:
        ref = next((arguments[n] for n in properties if is_ref(n) and n in arguments), None)
        if ref:
            arguments["name"], sources["name"] = element_name(ctx.page, ref), "page"
    if value_picks:
        _, answers = await _ask(ctx, {}, value_picks, next_action={**head, **arguments})
        apply(answers)

    # Arrays of strings (options to select, files to upload): among the options of the chosen element
    # when it has any, otherwise among the candidates from the goal and the page.
    for name in later:
        detail = properties[name].get("description", "")
        optional = name not in required
        ref = next((arguments[n] for n in properties if is_ref(n) and n in arguments), None)
        offered = options_under(ctx.page, ref) if ref else []
        if offered:
            pick = _value_pick(tool, name, detail, lambda _t, o=offered: o, {})
            origin = "page"
        else:
            pick = _value_pick(tool, name, detail, lambda t: [*goal_parts, *page_names(t)], {})
            origin = "goal"
        _, answers = await _ask(ctx, {}, {name: pick}, next_action={**head, **arguments})
        choice, confidence = answers.get(name, (NONE, 0.0))
        if choice != NONE and confidence >= MIN_CONFIDENCE:
            arguments[name] = [choice]
            sources[name] = origin if choice not in goal_parts else "goal"
            ctx.log(f"    typesafe: {name} = {choice!r} (confidence {confidence:.2f})")
        elif not optional:
            ctx.log(f"    typesafe: {name} has no fitting value")

    # Arrays of objects (the fields of browser_fill_form): entry by entry, each about another element,
    # while TypeSafe says that one more is needed.
    for name in objects:
        entries: list[dict] = []
        used = set(ctx.failed_refs)
        for index in range(MAX_ENTRIES):
            if index:
                more, _ = await _ask(
                    ctx,
                    {
                        "more": Noul(
                            instructions=(
                                f"After the `{name}` chosen so far, one more entry should be added to "
                                f"the same `{tool.name}` call to make progress toward `goal`."
                            )
                        )
                    },
                    {},
                    next_action={**head, **arguments, name: entries},
                )
                if more.nouls["more"].noul < 0.5:
                    break
            entry = await _decide_object(
                replace(ctx, failed_refs=frozenset(used)),
                tool,
                properties[name]["items"],
                {**outer, **arguments, name: entries},
                item=True,
            )
            if entry.unusable:
                break
            entries.append(entry.arguments)
            used.update(str(v) for k, v in entry.arguments.items() if is_ref(k))
            sources[name] = "page" if "page" in entry.sources.values() else "goal"
        if entries:
            arguments[name] = entries

    for first, second in exclusive_pairs(properties):
        if first in arguments and second in arguments:  # the schema allows one of them: the first in its order
            ctx.log(f"    {second} is left out: give either {first} or {second}, not both")
            del arguments[second], sources[second]

    missing = [n for n in required if n not in arguments and n not in OMITTED]
    if missing:
        return Decision(arguments, sources, f"no value for {', '.join(missing)}")
    if properties and not required and not arguments and tool.name != "browser_snapshot" and not item:
        return Decision(arguments, sources, "nothing to specify")
    return Decision(arguments, sources, None)
