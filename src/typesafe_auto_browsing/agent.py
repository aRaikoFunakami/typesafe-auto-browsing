"""観察 -> 判断 -> 実行のループ。

TypeSafe が、目的が終わったか、次に呼ぶツール（スキーマが許す任意のツール。要素を詳しく見るための
browser_snapshot も含む）、呼び出しの全引数を判断する。何も生成しない: 値は、選択肢・目的文の一部・
ページの文字列の中から選び、そのまま写す。Playwright MCP の出力とツールのスキーマは、そのまま使う。
"""

import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from mcp import ClientSession
from mcp.types import Tool
from typesafe_sdk import Choice, Noul

from .arguments import Context, Decision, ask_fitting_page, build_state, decide, is_ref, modal_handlers, unusable_reason
from .answer import ANSWER_ATTEMPTS, MIN_ANSWER_CONFIDENCE, SETTLE_SECONDS, Answers, find_answers
from .page_view import view_page
from .playwright_mcp import call_tool
from .usage import MeteredClient

SNAPSHOT = "browser_snapshot"
CLOSE = "browser_close"
ATTACH_CHARS = 20_000  # これより長いツール出力は、トレースがファイルとして保存する
FOCUS_CHARS = 8_000  # TypeSafe に見せる、読み取り専用ツールの出力の最大長（トレースには全文が残る）
MAX_RETRIES = 3  # 選んだツールが使えないとき、1 ステップの中でツールを選び直す回数
MAX_DEAD_STEPS = 3  # どのツールも使えなかったステップが、この回数続いたら失敗にする
ERROR_LINES = 4  # ツールが失敗した理由として TypeSafe に見せる、エラーの行数
_PAGE = re.compile(r"- Page (?:URL|Title): .*")

Log = Callable[[str], None]
Confirm = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True)
class Settings:
    max_steps: int = 20
    done_threshold: float = 0.8  # 「目的達成」の確率がこの値に達したら終了する
    page_chars: int = 50_000  # 最初に TypeSafe に見せるページの最大文字数。TypeSafe の窓に合わせて調整される


@dataclass(frozen=True)
class Outcome:
    success: bool
    reason: str
    page: str = ""  # 最後に読めたページ
    history: tuple[str, ...] = ()


async def _call_and_trace(client: MeteredClient, session: ClientSession, name: str, arguments: dict):
    """call_tool を呼び、その呼び出しと出力の全文を記録する。"""
    client.trace.event("mcp_call", tool=name, arguments=arguments)
    started = time.monotonic()
    text, is_error = await call_tool(session, name, arguments)
    duration = round(time.monotonic() - started, 3)
    if len(text) > ATTACH_CHARS:  # 例: ページのスナップショット。全文をトレースの隣のファイルに残す
        file = client.trace.attach(f"{client.trace.next_seq:04d}-{name}.yml", text)
        client.trace.event("mcp_result", tool=name, is_error=is_error, chars=len(text), text_file=file, duration_s=duration)
    else:
        client.trace.event("mcp_result", tool=name, is_error=is_error, text=text, duration_s=duration)
    return text, is_error


def is_read_only(tool: Tool) -> bool:
    """Playwright MCP は、ページを見るだけのツール（スナップショット、タブ一覧など）を読み取り専用と印付けする。"""
    return bool(tool.annotations and getattr(tool.annotations, "read_only_hint", None))


def _page_id(page: str) -> str:
    """ページの URL とタイトル。2 つのページを見分けるもの。"""
    return "\n".join(_PAGE.findall(page[:2_000]))


def _tool_question(tools: dict[str, Tool]) -> Choice:
    """次のツールの質問: 選択肢はツール名で、説明はツール自身の説明を使う。"""
    return Choice(
        instructions=(
            "Which browser tool should be called next to make progress toward `goal`, given "
            "`history` and the current `page`?"
        ),
        criteria={name: t.description for name, t in tools.items()},
    )


def _error_reason(text: str) -> str:
    """MCP のエラーの先頭の行。呼び出しが失敗した理由として TypeSafe に見せられる短さにする。"""
    lines = [line.strip() for line in text.removeprefix("### Error").strip().splitlines() if line.strip()]
    return " | ".join(lines[:ERROR_LINES])[:400]


def _describe(action: str, decision: Decision, page: str) -> str:
    """これから起きること。人が確認するためのもの。"""
    notes: list[str] = []

    def add(arguments: dict, sources: dict, indent: str = "") -> None:
        for name, value in arguments.items():
            if is_ref(name):
                line = next((l.strip() for l in page.splitlines() if f"[ref={value}]" in l), "")
                notes.append(f"{indent}{name} = {line[:100]}")
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                for entry in value:
                    add(entry, {}, indent + "  ")
                    notes.append(f"{indent}  --")
            elif sources.get(name) == "page":
                notes.append(f"{indent}{name} is text taken from the page")

    add(decision.arguments, decision.sources)
    return action + "".join(f"\n      {note}" for note in notes)


GOAL_ACHIEVED = Noul(
    instructions=(
        "The goal in `goal` has been achieved: the current `page` already "
        "shows the outcome the goal asks for, or, when the goal asks to find something, the "
        "information it is found from (the page need not point out the answer)."
    ),
    criteria={
        "true": "The page shows the requested outcome itself, such as the results, or the information to find the answer in.",
        "false": (
            "More steps are needed: the site is not open yet, fields are "
            "still empty, or a search has not been submitted yet."
        ),
    },
)


async def run_agent(
    client: MeteredClient,
    session: ClientSession,
    tools: list[Tool],
    goal: str,
    settings: Settings,
    log: Log = print,
    confirm: Confirm | None = None,
) -> Outcome:
    offered: dict[str, Tool] = {}
    for tool in tools:
        if reason := unusable_reason(tool):
            log(f"(not offered: {tool.name}: {reason})")
        else:
            offered[tool.name] = tool
    if not offered:
        return Outcome(False, "no tool can be used")

    # 1 ステップ: 1. ページのスナップショット  2. TypeSafe が「完了か・次のツールは」を判断  3. TypeSafe が引数を判断  4. ツールを呼ぶ
    history: list[str] = []
    attempts: Counter[tuple[str, str]] = Counter()
    failed: dict[str, set[str]] = defaultdict(set)  # ツールが失敗した ref。ページが変わるまで使わない
    page_chars = settings.page_chars
    snapshot = ""  # 最後に読めたページ
    focus = ""  # 最後に呼んだ読み取り専用ツールの出力
    last_output = ""
    dead_steps = 0
    for step in range(1, settings.max_steps + 1):
        text, is_error = await _call_and_trace(client, session, SNAPSHOT, {})
        handlers = modal_handlers(text) if is_error else []
        if not is_error or not handlers:
            snapshot = text
        extra_state: dict = {}
        if handlers:  # ダイアログやファイル選択が開いている: そのツールだけが操作でき、ページは読めない
            extra_state["modal"] = text
        if focus:
            extra_state["focus"] = focus

        view = await view_page(client, goal, history, snapshot, page_chars)
        page = view.text
        if view.parts > 1:
            log(f"    page: {len(snapshot):,} chars; showing parts {view.shown} of {view.parts}")
        available = {n: t for n, t in offered.items() if n in handlers} if handlers else offered
        available = available or offered

        start = min(page_chars, len(page))
        response, _, limit = await ask_fitting_page(
            client,
            page,
            start,
            lambda t: (build_state(goal, history, t, **extra_state), {"done": GOAL_ACHIEVED, "tool": _tool_question(available)}),
        )
        # TypeSafe が長いページを受け付けなかった: 入った長さを覚え、あとでまた長いページを試す
        page_chars = limit if limit < start else min(settings.page_chars, page_chars * 2)
        done = response.nouls["done"].noul
        if history and done >= settings.done_threshold:
            return Outcome(True, f"goal achieved (p={done:.2f})", snapshot, tuple(history))

        tool_choice = response.choices["tool"]
        log(f"[{step}] typesafe: {tool_choice.choice}  (tool confidence {tool_choice.confidence:.2f}, done p={done:.2f})")
        tool: Tool | None = None
        decision = Decision({}, {})
        excluded: dict[str, str] = {}
        for attempt in range(MAX_RETRIES + 1):
            candidate = available[tool_choice.choice]
            context = Context(
                client, goal, history, page, limit, log, extra_state, last_output, frozenset(failed[candidate.name])
            )
            decision = await decide(context, candidate)
            if not decision.unusable:
                tool = candidate
                break
            excluded[candidate.name] = decision.unusable
            log(f"    {candidate.name} cannot be used now: {decision.unusable}")
            remaining = {n: t for n, t in available.items() if n not in excluded}
            if not remaining or attempt == MAX_RETRIES:
                break
            again, _, _ = await ask_fitting_page(
                client, page, limit, lambda t: (build_state(goal, history, t, **extra_state), {"tool": _tool_question(remaining)})
            )
            tool_choice = again.choices["tool"]
            log(f"    typesafe: {tool_choice.choice}  (tool confidence {tool_choice.confidence:.2f})")

        if tool is None:
            dead_steps += 1
            reasons = "; ".join(f"{n}: {r}" for n, r in excluded.items())
            history.append(f"(no tool could be used: {reasons})")
            if dead_steps >= MAX_DEAD_STEPS:
                return Outcome(False, f"no tool could be used for {dead_steps} steps in a row ({reasons})")
            continue
        dead_steps = 0

        arguments = decision.arguments
        action = f"{tool.name} {json.dumps(arguments, ensure_ascii=False)}"
        key = (action, _page_id(snapshot))  # 同じページで同じ呼び出しが 2 回を超えたら、行き詰まりとみなす
        attempts[key] += 1
        if attempts[key] > 2:
            return Outcome(False, f"stuck: repeated {action}")
        if confirm and not is_read_only(tool):
            if not await confirm(_describe(action, decision, page)):
                log(f"    declined: {action}")
                history.append(f"{action} -> DECLINED by the user")
                continue
        log(f"    mcp: {action}")
        output, is_error = await _call_and_trace(client, session, tool.name, arguments)
        last_output = output
        if is_error:
            reason = _error_reason(output)
            log(f"    mcp: failed: {reason}")
            history.append(f"{action} -> FAILED: {reason}")
            failed[tool.name].update(str(v) for n, v in arguments.items() if is_ref(n))
            continue
        log("    mcp: ok")
        history.append(action)
        if tool.name == CLOSE:
            return Outcome(False, "the browser was closed")
        if is_read_only(tool):
            # 出力が求めていたもの。ページ全体のスナップショットは、どのステップでも撮り直す
            focus = "" if tool.name == SNAPSHOT and "target" not in arguments else output[:FOCUS_CHARS]
        else:
            focus = ""
            failed.clear()
    return Outcome(False, f"step limit ({settings.max_steps}) reached")


async def answer_goal(client: MeteredClient, session: ClientSession, goal: str, outcome: Outcome, log: Log) -> Answers:
    """目的の答えを、今のページから読み取る。

    実行が終わったページはまだ読み込み中のことがある（並べ替えたばかりの検索など）ので、答えのために
    ページを読み直す。答えが疑わしいときは、少し待ってからまた読むが、ページが変わり続けている間だけ:
    落ち着いたページには、もう聞かない。"""
    page = outcome.page
    previous = None
    answers = Answers(False, "not asked")
    for attempt in range(ANSWER_ATTEMPTS):
        text, is_error = await _call_and_trace(client, session, SNAPSHOT, {})
        if not is_error:
            page = text
        if page == previous:  # 前回の読み取りから変わっていない: ページは読み込み済みなので、答えはそのまま
            log("    answer: the page has not changed since it was last read")
            break
        previous = page
        answers = await find_answers(client, goal, list(outcome.history), page)
        if not answers.wanted or (answers.candidates and answers.candidates[0].confidence >= MIN_ANSWER_CONFIDENCE):
            break
        if attempt + 1 < ANSWER_ATTEMPTS:
            log(f"    answer: not sure yet ({answers.reason}); the page may still be loading, reading it again")
            await _call_and_trace(client, session, "browser_wait_for", {"time": SETTLE_SECONDS})
    return answers
