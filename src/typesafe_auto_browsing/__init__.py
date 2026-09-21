import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError

from .agent import Settings, run_agent
from .playwright_mcp import playwright_session
from .trace import Trace
from .usage import MeteredClient, Usage

# 実行の記録の置き場所。インストールした CLI が、実行した場所にファイルをばらまかないようにする。
HOME = Path(os.environ.get("TYPESAFE_AUTO_BROWSING_HOME", "~/.typesafe-auto-browsing")).expanduser()

# 目的文の最大文字数。値の候補は目的文の語の連続すべてで、語の数の 2 乗で増える。候補が約 1,400 個を超えると
# TypeSafe の文脈（32k トークン）に入らない（日本語の文で、165 文字は通り 175 文字は失敗した）。
MAX_GOAL_CHARS = 120


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="typesafe-auto-browsing",
        description="Achieve a goal in Chrome via Playwright MCP, with TypeSafe choosing the tools.",
    )
    parser.add_argument("goal", nargs="*", help="What you want to achieve in the browser")
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Read the goal from a file (lines starting with # are comments), e.g. prompts/amazon-cheapest-usbc.txt",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as one JSON object on stdout (progress goes to stderr)",
    )
    parser.add_argument("--max-steps", type=int, default=Settings.max_steps)
    parser.add_argument(
        "--done-threshold",
        type=float,
        default=Settings.done_threshold,
        help="Finish when the goal-achieved probability reaches this (default: 0.8)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=HOME / "logs",
        help=f"Directory for the full record of the run, one JSONL file per run (default: {HOME / 'logs'}; set TYPESAFE_AUTO_BROWSING_HOME to move it)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Ask before each tool call that changes the page, showing the tool and its arguments (needs a terminal)",
    )
    parser.add_argument("--headless", action="store_true", help="Run Chrome without a window")
    return parser.parse_args()


def goal_from_file(path: Path) -> str:
    """プロンプトファイルの目的: コメント（# で始まる行）と空行を除いた行"""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return " ".join(line for line in lines if line and not line.startswith("#"))


def _read_goal(words: list[str], file: Path | None = None) -> str:
    if file and words:
        sys.exit("error: give the goal either as words or with --file, not both")
    if file:
        try:
            goal = goal_from_file(file)
        except OSError as error:
            sys.exit(f"error: cannot read {file}: {error.strerror}")
        if not goal:
            sys.exit(f"error: {file} has no goal (only comments or blank lines)")
    else:
        goal = " ".join(words).strip()
        if not goal and sys.stdin.isatty():
            goal = input("Goal: ").strip()
        if not goal:
            sys.exit("error: a goal is required")
    if len(goal) > MAX_GOAL_CHARS:
        sys.exit(f"error: the goal is {len(goal)} characters; the limit is {MAX_GOAL_CHARS}. Split it into one goal per stage")
    return goal


async def _confirm(description: str) -> bool:
    """ページを変更するツール呼び出しの前に、人に確認する。"""
    answer = await asyncio.to_thread(input, f"  confirm: {description}\n  proceed? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


async def _run(args: argparse.Namespace, goal: str, trace: Trace) -> bool:
    """ブラウザを開き、エージェントを実行する。"""
    async with playwright_session(args.headless, HOME / "playwright") as session:
        tools = (await session.list_tools()).tools
        trace.event("mcp_tools", tools=[t.model_dump(mode="json") for t in tools])
        usage = Usage()
        async with AsyncTypeSafeClient() as typesafe:
            client = MeteredClient(typesafe, usage, trace)

            confirming = args.confirm
            out = sys.stderr if args.json else sys.stdout  # --json のとき、標準出力は JSON だけにする
            print(f"Goal: {goal}\nTrace: {trace.path}\nConfirmation before each change: {'on' if confirming else 'off'}\n", file=out)

            def log(line: str) -> None:
                trace.event("log", line=line)
                print(line, file=out, flush=True)

            # ブラウザを実際に操作する本体。目的が達成されたと TypeSafe が判断する（done の確率がしきい値に届く）か、
            # 最大ステップ数に達するまで、「ページを見る → 次のツールと引数を TypeSafe が決める → 実行」を繰り返す。
            # 戻り値の Outcome は、成功か失敗か・その理由・最後に読めたページ・操作の履歴。
            outcome = await run_agent(
                client,  # TypeSafe への問い合わせ（使用量とトレースを記録する）
                session,  # Playwright MCP のセッション（ブラウザの操作）
                tools,  # 使えるブラウザツールの一覧
                goal,
                Settings(args.max_steps, args.done_threshold),  # 最大ステップ数と、達成とみなす確率
                log,  # 進行の表示（画面とトレースの両方に残す）
                _confirm if confirming else None,  # --confirm のとき、ページを変える操作の前に人に確認する関数
            )
        snapshot = _save_snapshot(trace, outcome.page)
        trace.event("outcome", success=outcome.success, reason=outcome.reason, snapshot=snapshot, usage=usage.as_dict())
        if args.json:  # --json: 結果を 1 つの JSON にして標準出力へ出し、人向けの表示と Enter 待ちは省いて終わる
            print(_result_json(goal, outcome.success, outcome.reason, outcome.page, snapshot, usage, trace))
            return outcome.success
        print(f"\n{'Done' if outcome.success else 'Failed'}: {outcome.reason}")
        if snapshot:
            print(f"Snapshot: {snapshot}")
        print(f"\n{usage.summary()}\nTrace: {trace.path}")
        if sys.stdin.isatty() and not args.headless:  # 端末で見ていて、ウィンドウがあるとき: 結果のページを確認できるよう、Enter までブラウザを閉じない
            await asyncio.to_thread(input, "Press Enter to close the browser...")
        return outcome.success


def _page_info(page: str, snapshot: str | None) -> dict:
    """実行が終わったページの URL とタイトル（スナップショットの先頭から読む）と、スナップショット全文のファイル。"""
    url = re.search(r"- Page URL: (.*)", page)
    title = re.search(r"- Page Title: (.*)", page)
    return {"url": url[1].strip() if url else None, "title": title[1].strip() if title else None, "snapshot": snapshot}


def _save_snapshot(trace: Trace, page: str) -> str | None:
    """最後のスナップショットを、加工せずに、トレースの隣のファイルへ。絶対パスを返す（呼び出し側の作業場所によらず開ける）。"""
    return str((trace.path.parent / trace.attach("final-snapshot.yml", page)).resolve()) if page else None


def _result_json(goal: str, success: bool, reason: str, page: str, snapshot: str | None, usage: Usage | None, trace: Trace) -> str:
    return json.dumps(
        {
            "goal": goal,
            "success": success,
            "reason": reason,
            "page": _page_info(page, snapshot),
            "usage": usage.as_dict() if usage else None,
            "trace": str(trace.path),
        },
        ensure_ascii=False,
        indent=2,
    )


def _flatten_group(error: BaseException) -> list[BaseException]:
    """（入れ子になりうる）例外グループの中の例外。"""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for nested in error.exceptions for leaf in _flatten_group(nested)]
    return [error]


async def _run_traced(args: argparse.Namespace, goal: str, trace: Trace) -> bool:
    try:
        return await _run(args, goal, trace)
    except BaseException as error:
        trace.event("error", errors=[f"{type(e).__name__}: {e}" for e in _flatten_group(error)])
        raise


def main() -> None:
    """エントリポイント: 目的を読み、実行し、目的を達成したら 0 で終了する。"""
    args = _parse_args()
    if args.confirm and not sys.stdin.isatty():
        sys.exit("error: --confirm asks on the terminal, but there is none")
    goal = _read_goal(args.goal, args.file)
    trace = Trace(args.log_dir)
    trace.event("run_start", goal=goal, args={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()})
    try:
        ok = asyncio.run(_run_traced(args, goal, trace))
    except* (TypeSafeError, RuntimeError) as group:  # MCP のタスクグループの中で送出される
        message = f"error: {_flatten_group(group)[0]}"
        if args.json:  # 落ちたときも、呼び出し側が読める JSON を返す
            print(_result_json(goal, False, message, "", None, None, trace))
        sys.exit(f"{message}\nTrace: {trace.path}")
    finally:
        trace.close()
    sys.exit(0 if ok else 1)
