"""ツール呼び出しの引数を決める。

引数とは何かは、ツールの `input_schema` だけが決める。TypeSafe が全ての値を選ぶ（要素の ref、enum、
boolean、選択肢、自由入力の文字列や数値。自由入力のものは、目的文とページから取った候補の中から選ぶ）。
コードは、選ばれた候補を写すだけで、何も生成しない。引数に候補がなければ、そのステップではそのツールは
使えず、呼び出し側が別のツールを選ぶ。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from mcp.types import Tool
from typesafe_sdk import Choice, Noul, TypeSafeBadRequestError

from .keys import KEYS
from .usage import MeteredClient

MAX_OPTIONS = 255  # 1 つの Choice の質問に入れられる選択肢の数
MIN_PAGE_CHARS = 2_000
MIN_CONFIDENCE = 0.5  # これより低い候補は、自由入力の値として使うには疑わしすぎる
MAX_NAME_CHARS = 100  # 値の候補として出す、ページの文字列の最大長
MAX_ENTRIES = 10  # オブジェクトの配列（例: browser_fill_form の fields）の、1 回の呼び出しでの項目数
NONE = "(none of the above)"
# 判断しない引数: `element` は人が読むためのラベルにすぎず、`filename` は出力を応答の外に出して
# しまい、`depth` はスナップショットの木を切ってしまう。
OMITTED = {"element", "filename", "depth"}
NAMED_VALUES = {("browser_press_key", "key"): KEYS}
PAGE_SUFFIX = "@page"  # ページの文字列から選ぶ質問。目的文から選ぶ質問と並べて聞く

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
    page: str  # TypeSafe に見せるページ
    limit: int  # `page` のうち、送る最大の長さ
    log: Log
    extra: dict = field(default_factory=dict)  # 追加の状態。例: 最後に呼んだ読み取り専用ツールの出力
    last_output: str = ""  # 最後に呼んだツールの出力の全文。そこに載っている番号を使うため
    failed_refs: frozenset[str] = frozenset()  # このツールがこのページですでに失敗した ref


@dataclass
class Decision:
    arguments: dict
    sources: dict[str, str]  # 引数 -> 値の出どころ: goal、page、list、schema
    unusable: str | None = None  # 値を選べなかった理由。呼び出し側は、別のツールを選ぶ


def modal_handlers(text: str) -> list[str]:
    """Playwright MCP の出力に出ている、モーダル状態（ダイアログ、ファイル選択）を扱えるツール。"""
    match = _MODAL.search(text)
    return list(dict.fromkeys(_HANDLER.findall(match[1]))) if match else []


# --- スキーマ ---------------------------------------------------------------------------


def _enum(spec: dict) -> list[str] | None:
    if spec.get("enum"):
        return list(spec["enum"])
    return next((list(alt["enum"]) for alt in spec.get("anyOf", []) if alt.get("enum")), None)


def _type(spec: dict) -> str | None:
    if spec.get("type"):
        return spec["type"]
    return next((alt.get("type") for alt in spec.get("anyOf", []) if alt.get("type") != "null"), None)


def exclusive_pairs(properties: dict) -> list[tuple[str, str]]:
    """同時に指定しないとスキーマが言っている引数の組（「text か regex のどちらか、両方は不可」）。"""
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
    """オブジェクト（配列の引数の 1 項目）を、値 1 つずつ埋められない理由。"""
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
    """スキーマだけから、選択でツールを埋められない理由。埋められるなら None。"""
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


# --- 候補 -------------------------------------------------------------------------------


def _char_class(char: str) -> str:
    code = ord(char)
    if code < 128:
        return "latin"  # 単語、URL、パス: 次のスペースか日本語の文字まで
    if 0x3040 <= code <= 0x309F:
        return "hiragana"
    if 0x30A0 <= code <= 0x30FF:
        return "katakana"
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
        return "kanji"
    return "other"


def goal_candidates(goal: str) -> list[str]:
    """値になりうる目的文の部分: 同じ種類の文字が続く範囲（単語、URL、漢字の語など）と、
    それらが連続する範囲のすべて。書かれたとおりに写す。"""
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
    """スナップショットの要素の名前: リンク、ボタン、見出しなどの文字列。"""
    names: dict[str, None] = {}
    for line in page.splitlines():
        if match := _NAME.search(line):
            name = match[1].replace('\\"', '"').replace("\\\\", "\\")
            if 0 < len(name) <= MAX_NAME_CHARS:
                names[name] = None
    return list(names)


def element_name(page: str, ref: str) -> str:
    """スナップショットの要素 `ref` の名前（名前がなければ role）。"""
    line = next((l for l in page.splitlines() if f"[ref={ref}]" in l), "")
    if match := _NAME.search(line):
        return match[1].replace('\\"', '"').replace("\\\\", "\\")
    return line.strip().removeprefix("- ").split(" ", 1)[0].rstrip(":") or ref


def _numbers(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\d+(?:\.\d+)?", text)))


def _refs(page: str, failed: frozenset[str]) -> list[str]:
    return [ref for ref in dict.fromkeys(_REF.findall(page)) if ref not in failed]


def options_under(page: str, ref: str) -> list[str]:
    """スナップショットで、要素 `ref` の下に入れ子になっている `option` 行のラベル。"""
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
            if text.startswith("'") and text.endswith("'"):  # YAML は、": " を含む文字列を引用符で囲む
                text = text[1:-1].replace("''", "'")
            if match := _OPTION.match(text):
                labels.append(match[1])
        return list(dict.fromkeys(labels))
    return []


# --- TypeSafe への質問 ---------------------------------------------------------------


async def ask_fitting_page(client: MeteredClient, page: str, limit: int, build: Callable):
    """`build(page[:limit]) -> (state, questions)` に対する system_one。

    TypeSafe の文脈は限られている（32k トークン）ので、入るまでページを半分にする。
    応答、受け付けられた state、使ったページの長さを返す。
    """
    while True:
        state, questions = build(page[:limit])
        try:
            return await client.system_one(state, questions), state, limit
        except TypeSafeBadRequestError as error:
            if "max_tokens_exceeded" not in str(error) or limit <= MIN_PAGE_CHARS:
                raise
            limit //= 2


def build_state(goal: str, history: list[str], page: str, **extra: object) -> dict:
    """TypeSafe が、どの質問でも読む状態: 目的、これまでにしたこと、ページ。"""
    return {"goal": goal, "history": history or ["(nothing done yet)"], "page": page, **extra}


@dataclass
class Pick:
    """選択肢が多い質問: TypeSafe が 1 つ選ぶか、NONE を選ぶ。"""

    options: Callable[[str], list[str]]  # 送るページの文字列に対して出す選択肢
    question: Callable[[list[str]], Choice]  # 選択肢（最大 MAX_OPTIONS 個）に対する質問
    optional: bool


def _chunks(options: list[str]) -> list[list[str]]:
    size = MAX_OPTIONS - 1  # NONE の分を空けておく
    return [options[i : i + size] for i in range(0, len(options), size)]


def _criteria(options: list[str], notes: dict[str, str] | None = None) -> dict[str, str | None]:
    criteria: dict[str, str | None] = {option: (notes or {}).get(option) for option in options}
    criteria[NONE] = "No option above is the value."
    return criteria


async def ask_picks(ctx: Context, questions: dict, picks: dict[str, Pick], **state_extra):
    """通常の質問と全ての pick を 1 回のリクエストで聞く。1 つの質問に入りきらない数の選択肢を持つ pick は
    塊に分けて聞き、塊の勝者どうしを 2 回目のリクエストで戦わせる。

    応答と、pick ごとの（選ばれた選択肢または NONE、確信度）を返す。"""

    def chunks_for(text: str) -> dict[str, list[list[str]]]:
        return {name: _chunks(pick.options(text)) for name, pick in picks.items()}

    def alone(name: str, chunk: list[str]) -> bool:
        return not picks[name].optional and len(chunk) == 1  # 選ぶ相手がいない

    def build(text: str):
        asked = dict(questions)
        for name, chunks in chunks_for(text).items():
            for i, chunk in enumerate(chunks):
                if not alone(name, chunk):
                    asked[f"{name}:{i}"] = picks[name].question(chunk)
        return build_state(ctx.goal, ctx.history, text, **ctx.extra, **state_extra), asked

    response, state, _ = await ask_fitting_page(ctx.client, ctx.page, ctx.limit, build)
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
        elif winners:  # 1 つの質問には多すぎる: 各塊の勝者の中から選ぶ
            names = list(dict.fromkeys(choice for choice, _ in winners if choice != NONE))
            if len(names) > 1:
                finalists[name] = names
            else:
                answers[name] = (names[0] if names else NONE, max(confidence for _, confidence in winners))
    if finalists:
        final, _, _ = await ask_fitting_page(
            ctx.client,
            text,
            len(text),
            lambda t: (
                build_state(ctx.goal, ctx.history, t, **ctx.extra, **state_extra),
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
    """現在のページで、`tool` の引数を決める。"""
    return await _decide_object(ctx, tool, tool.input_schema, {})


async def _decide_object(ctx: Context, tool: Tool, schema: dict, outer: dict, item: bool = False) -> Decision:
    """`schema` が表すオブジェクトの値を決める: ツールの引数、または配列の引数の 1 項目。
    `outer` は、その周りですでに決まっているもので、操作として TypeSafe に見せる。"""
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    action_so_far = {"tool": tool.name, **outer}
    goal_parts = goal_candidates(ctx.goal)
    goal_numbers = _numbers(ctx.goal)
    # ツールの出力に載っている番号: タブ（`- 0: (current) ...`）、リクエスト（`1. [GET] ...`）。
    listed_numbers = _INDEX.findall(ctx.last_output) + _INDEX.findall(ctx.page[:3_000])
    object_arrays: list[str] = []  # オブジェクトの配列: 項目を 1 つずつ選ぶ
    name_of_element = False  # 項目の、人が読むための `name`: その要素の名前

    arguments: dict[str, object] = {}
    sources: dict[str, str] = {}
    questions: dict[str, Choice | Noul] = {}
    picks: dict[str, Pick] = {}
    string_arrays: list[str] = []  # 配列の引数: 属する要素を選んだあとで選ぶ
    kinds: dict[str, str] = {}

    # スキーマの各プロパティが、型に応じて TypeSafe への質問になる: 要素・文字列・数値は候補から選び、
    # enum は選択、boolean は yes/no。
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
            string_arrays.append(name)
        elif kind == "array" and _type(spec.get("items", {})) == "object":
            if optional:
                continue
            object_arrays.append(name)
        elif kind in ("integer", "number"):
            numbers = list(dict.fromkeys([*goal_numbers, *(listed_numbers if name == "index" else [])]))
            notes = {n: "listed in the last output" for n in listed_numbers if n not in goal_numbers}
            picks[name] = _value_pick(tool, name, detail, lambda _t, n=numbers: n, notes)
            kinds[name] = kind
        elif kind == "string" and _needs_code(spec):
            continue  # コードは選べない
        elif kind == "string" and item and name == "name" and any(is_ref(n) for n in properties):
            name_of_element = True
        elif kind == "string":
            # 先に目的文: 数の少ない目的文の部分と、数の多いページの文字列は別々に聞き、ページの文字列は、
            # 目的文のどの部分も値でないときだけ使う。
            named = [*NAMED_VALUES.get((tool.name, name), ()), *re.findall(r"`(true|false)`", detail)]
            from_goal = list(dict.fromkeys([*named, *goal_parts]))
            picks[name] = _value_pick(tool, name, detail, lambda _t, c=from_goal: c, {})
            picks[f"{name}{PAGE_SUFFIX}"] = _value_pick(
                tool, name, detail, lambda t, g=from_goal: [n for n in page_names(t) if n not in g], {}
            )
            kinds[name] = kinds[f"{name}{PAGE_SUFFIX}"] = "string"
        elif optional:
            continue  # 省略できる構造体: 出さない
        else:
            return Decision({}, {}, f"`{name}` has a type that cannot be chosen ({kind})")

    if not properties:
        return Decision({}, {}, None)

    def apply_picks(answers: dict[str, tuple[str, float]]) -> None:
        ordered = sorted(answers, key=lambda n: n.endswith(PAGE_SUFFIX))  # 目的文からの選択を先に
        for pick_name in ordered:
            choice, confidence = answers[pick_name]
            name = pick_name.removesuffix(PAGE_SUFFIX)
            if name in arguments:  # 目的文からすでに決まっている
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

    # 先に選択と要素、それから値。値は、操作の対象の要素がわかった状態で TypeSafe が決める
    # （入力する文字列は、入れる先の入力欄によって変わる）。
    ref_picks = {n: p for n, p in picks.items() if kinds[n] == "ref"}
    value_picks = {n: p for n, p in picks.items() if kinds[n] != "ref"}
    if questions or ref_picks:
        response, answers = await ask_picks(ctx, questions, ref_picks, next_action=action_so_far if outer else tool.name)
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
        apply_picks(answers)
    if name_of_element:
        ref = next((arguments[n] for n in properties if is_ref(n) and n in arguments), None)
        if ref:
            arguments["name"], sources["name"] = element_name(ctx.page, ref), "page"
    if value_picks:
        _, answers = await ask_picks(ctx, {}, value_picks, next_action={**action_so_far, **arguments})
        apply_picks(answers)

    # 文字列の配列（選ぶ選択肢、アップロードするファイル）: 選んだ要素に選択肢があればその中から、
    # なければ、目的文とページからの候補の中から。
    for name in string_arrays:
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
        _, answers = await ask_picks(ctx, {}, {name: pick}, next_action={**action_so_far, **arguments})
        choice, confidence = answers.get(name, (NONE, 0.0))
        if choice != NONE and confidence >= MIN_CONFIDENCE:
            arguments[name] = [choice]
            sources[name] = origin if choice not in goal_parts else "goal"
            ctx.log(f"    typesafe: {name} = {choice!r} (confidence {confidence:.2f})")
        elif not optional:
            ctx.log(f"    typesafe: {name} has no fitting value")

    # オブジェクトの配列（browser_fill_form の fields）: 1 項目ずつ、それぞれ別の要素について、
    # TypeSafe がもう 1 つ要ると言う間、続ける。
    for name in object_arrays:
        entries: list[dict] = []
        used = set(ctx.failed_refs)
        for index in range(MAX_ENTRIES):
            if index:
                more, _ = await ask_picks(
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
                    next_action={**action_so_far, **arguments, name: entries},
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
        if first in arguments and second in arguments:  # スキーマが片方だけを許す: 順序が先のほうを残す
            ctx.log(f"    {second} is left out: give either {first} or {second}, not both")
            del arguments[second], sources[second]

    missing = [n for n in required if n not in arguments and n not in OMITTED]
    if missing:
        return Decision(arguments, sources, f"no value for {', '.join(missing)}")
    if properties and not required and not arguments and tool.name != "browser_snapshot" and not item:
        return Decision(arguments, sources, "nothing to specify")
    return Decision(arguments, sources, None)
