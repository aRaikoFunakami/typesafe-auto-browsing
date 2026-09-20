"""観察 -> 判断 -> 実行のループ。

TypeSafe が、目的が終わったか、次に呼ぶツール（スキーマが許す任意のツール。要素を詳しく見るための
browser_snapshot も含む）、呼び出しの全引数を判断する。何も生成しない: 値は、選択肢・目的文の一部・
ページの文字列の中から選び、そのまま写す。Playwright MCP の出力とツールのスキーマは、そのまま使う。
"""

import asyncio
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
from .page_view import view_page
from .playwright_mcp import call_tool
from .usage import MeteredClient

SNAPSHOT = "browser_snapshot"
CLOSE = "browser_close"
ATTACH_CHARS = 20_000  # これより長いツール出力は、トレースがファイルとして保存する
FOCUS_CHARS = 8_000  # TypeSafe に見せる、読み取り専用ツールの出力の最大長（トレースには全文が残る）
MAX_RETRIES = 3  # 選んだツールが使えないとき、1 ステップの中でツールを選び直す回数
MAX_DEAD_STEPS = 3  # どのツールも使えなかったステップが、この回数続いたら失敗にする
SETTLE_SECONDS = 1.0  # ページを変える操作のあと、スナップショットを取り直すまでの間隔
SETTLE_ATTEMPTS = 5  # 取り直す回数の上限。前回と同じになったら、その前に止める
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


_VOLATILE = re.compile(r"\[ref=\w+\]|\d+")  # 落ち着いたかを比べるときに無視する: ref の番号、数字（カウントダウンなど）


def _shape(text: str) -> str:
    return _VOLATILE.sub("", text)


async def _snapshot(client: MeteredClient, session: ClientSession, settle: bool):
    """ページのスナップショット。settle（ページを変える操作の直後）のときは、前回と同じになるまで取り直す。

    操作は、画面の更新（並べ替えたあとの一覧など）が終わる前に戻ることがある。すぐ取ると、更新の途中のページで
    「目的は達成済みか」を判断してしまう。TypeSafe には聞かず、MCP を呼ぶだけ。
    比べるのは ref と数字を除いた形（1 秒ごとに変わるカウントダウンで、いつまでも落ち着かないのを避ける）。返すのは、
    取れたままのテキスト。
    ponytail: 形が前回と同じ = 落ち着いた、とみなす。読み込み中で変化がない間に当たると早すぎ、数字だけの更新は見逃す。
    待つ文字が分かるなら browser_wait_for のほうが確実。"""
    text, is_error = await _call_and_trace(client, session, SNAPSHOT, {})
    for _ in range(SETTLE_ATTEMPTS if settle else 0):
        if is_error:  # ダイアログが開いている: 読めないので、待っても同じ
            break
        await asyncio.sleep(SETTLE_SECONDS)
        again, is_error = await _call_and_trace(client, session, SNAPSHOT, {})
        settled = _shape(again) == _shape(text)
        text = again
        if settled:
            break
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
    """目的が達成されるまで、ブラウザのツールを 1 つずつ選んで呼ぶ。

    1 ステップは、ページを読む -> TypeSafe に「目的は達成済みか」と「次に呼ぶツール」を聞く ->
    そのツールの引数を TypeSafe に決めさせる -> ツールを呼ぶ。次のどれかで終わる:
    達成済み（成功）、使えるツールがない・行き詰まった・ブラウザを閉じた・最大ステップ数に達した（失敗）。
    """
    # 使えないツール（スキーマを扱えないものなど）を除いた、TypeSafe に選ばせるツール
    offered: dict[str, Tool] = {}
    for tool in tools:
        if reason := unusable_reason(tool):
            log(f"(not offered: {tool.name}: {reason})")
        else:
            offered[tool.name] = tool
    if not offered:
        return Outcome(False, "no tool can be used")

    # 1 ステップ: 1. ページのスナップショット  2. TypeSafe が「完了か・次のツールは」を判断  3. TypeSafe が引数を判断  4. ツールを呼ぶ
    history: list[str] = []  # ここまでの操作（と失敗）。毎回 TypeSafe に見せる
    attempts: Counter[tuple[str, str]] = Counter()
    failed: dict[str, set[str]] = defaultdict(set)  # ツールが失敗した ref。ページが変わるまで使わない
    page_chars = settings.page_chars
    snapshot = ""  # 最後に読めたページ
    focus = ""  # 最後に呼んだ読み取り専用ツールの出力
    last_output = ""  # 最後に呼んだツールの出力（引数を決めるときの手がかり）
    dead_steps = 0  # どのツールも使えなかったステップが、続けて何回あったか
    changed = False  # 直前の操作がページを変えたか。変えたなら、次のスナップショットは落ち着くまで待つ
    for step in range(1, settings.max_steps + 1):
        # (1) ページを読む。スナップショットが失敗してダイアログの案内が返ったときは、ダイアログを閉じられるツールだけを使う
        text, is_error = await _snapshot(client, session, changed)
        changed = False
        handlers = modal_handlers(text) if is_error else []
        if not is_error or not handlers:
            snapshot = text
        extra_state: dict = {}
        if handlers:  # ダイアログやファイル選択が開いている: そのツールだけが操作でき、ページは読めない
            extra_state["modal"] = text
        if focus:
            extra_state["focus"] = focus

        # 長いページは、目的に関係する部分だけに絞って TypeSafe に見せる
        view = await view_page(client, goal, history, snapshot, page_chars)
        page = view.text
        if view.parts > 1:
            log(f"    page: {len(snapshot):,} chars; showing parts {view.shown} of {view.parts}")
        # このステップで選べるツール。ダイアログが開いているときは、それを扱えるものだけ
        available = {n: t for n, t in offered.items() if n in handlers} if handlers else offered
        available = available or offered

        # (2) TypeSafe に 2 つ聞く: 「目的は達成済みか」（done）と「次に呼ぶツールは何か」（tool）
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
        # 1 ステップ目（history が空）だけは、done が高くても完了にしない。まだ何も操作していないので、
        # 開いているのは最初のページ（空白ページなど）で、目的が達成済みのはずがない。ここで完了にすると、
        # 何もせずに成功で終わり、しかも空白ページが結果のページとして返ってしまう。
        # 目的の文面だけで done が高く出る誤判定を、最初の 1 回は無視する。2 ステップ目以降は、
        # 必ず history に 1 行増えている（成功・失敗・拒否・使えるツールなし、のどれでも）ので、常に判定される。
        if history and done >= settings.done_threshold:
            return Outcome(True, f"goal achieved (p={done:.2f})", snapshot, tuple(history))

        tool_choice = response.choices["tool"]
        log(f"[{step}] typesafe: {tool_choice.choice}  (tool confidence {tool_choice.confidence:.2f}, done p={done:.2f})")
        # (3) 選ばれたツールの引数を決める。今のページでは使えないと分かったら、そのツールを除いて選び直す（最大 MAX_RETRIES 回）
        tool: Tool | None = None
        decision = Decision({}, {})
        excluded: dict[str, str] = {}  # 使えなかったツールと、その理由
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

        if tool is None:  # 選び直しても、使えるツールがなかった。理由を履歴に残して次のステップへ
            dead_steps += 1
            reasons = "; ".join(f"{n}: {r}" for n, r in excluded.items())
            history.append(f"(no tool could be used: {reasons})")
            if dead_steps >= MAX_DEAD_STEPS:  # これが続くなら、諦める
                return Outcome(False, f"no tool could be used for {dead_steps} steps in a row ({reasons})", snapshot, tuple(history))
            continue
        dead_steps = 0

        # (4) ツールを呼ぶ
        arguments = decision.arguments
        action = f"{tool.name} {json.dumps(arguments, ensure_ascii=False)}"
        key = (action, _page_id(snapshot))  # 同じページで同じ呼び出しが 2 回を超えたら、行き詰まりとみなす
        attempts[key] += 1
        if attempts[key] > 2:
            return Outcome(False, f"stuck: repeated {action}", snapshot, tuple(history))
        if confirm and not is_read_only(tool):  # --confirm のとき、ページを変える操作だけ、人に確認する
            if not await confirm(_describe(action, decision, page)):
                log(f"    declined: {action}")
                history.append(f"{action} -> DECLINED by the user")
                continue
        log(f"    mcp: {action}")
        output, is_error = await _call_and_trace(client, session, tool.name, arguments)
        last_output = output
        if is_error:  # 失敗: 理由を履歴に残す（次のステップで TypeSafe が見て、別の手を選ぶ）。同じ要素は、ページが変わるまで使わない
            reason = _error_reason(output)
            log(f"    mcp: failed: {reason}")
            history.append(f"{action} -> FAILED: {reason}")
            failed[tool.name].update(str(v) for n, v in arguments.items() if is_ref(n))
            continue
        log("    mcp: ok")
        history.append(action)
        if tool.name == CLOSE:  # 目的の途中でブラウザを閉じたら、続けられない
            return Outcome(False, "the browser was closed", snapshot, tuple(history))
        if is_read_only(tool):  # ページを見るだけのツール: 出力を、次のステップで TypeSafe に見せる
            # 出力が求めていたもの。ページ全体のスナップショットは、どのステップでも撮り直す
            focus = "" if tool.name == SNAPSHOT and "target" not in arguments else output[:FOCUS_CHARS]
        else:  # ページを変える操作をした: 出力はもう古く、失敗した要素も、ページが変われば使えるかもしれない
            focus = ""
            failed.clear()
            changed = True
    return Outcome(False, f"step limit ({settings.max_steps}) reached", snapshot, tuple(history))
