import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError

from .agent import Settings, answer_goal, run_agent
from .answer import as_dicts  # `answer` にしない: サブモジュールの名前と同じになるため
from .playwright_mcp import playwright_session
from .trace import Trace
from .usage import MeteredClient, Usage


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
        default=Path("logs"),
        help="Directory for the full record of the run, one JSONL file per run (default: logs)",
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
        return goal
    goal = " ".join(words).strip()
    if not goal and sys.stdin.isatty():
        goal = input("Goal: ").strip()
    if not goal:
        sys.exit("error: a goal is required")
    return goal


async def _confirm(description: str) -> bool:
    """ページを変更するツール呼び出しの前に、人に確認する。"""
    answer = await asyncio.to_thread(input, f"  confirm: {description}\n  proceed? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


async def _run(args: argparse.Namespace, goal: str, trace: Trace) -> bool:
    """ブラウザを開き、エージェントの実行と答えの読み取りを行う。"""
    async with playwright_session(args.headless) as session:
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
            answers = None
            if outcome.success:
                answers = await answer_goal(client, session, goal, outcome, log)
                trace.event("answers", wanted=answers.wanted, reason=answers.reason, candidates=as_dicts(answers))
        trace.event("outcome", success=outcome.success, reason=outcome.reason, usage=usage.as_dict())
        if args.json:  # --json: 結果を 1 つの JSON にして標準出力へ出し、人向けの表示と Enter 待ちは省いて終わる
            print(
                json.dumps(
                    {
                        "goal": goal,
                        "success": outcome.success,
                        "reason": outcome.reason,
                        "page": _page_info(outcome.page),
                        "answers": as_dicts(answers) if answers else [],
                        "answers_note": answers.reason if answers else None,
                        "usage": usage.as_dict(),
                        "trace": str(trace.path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return outcome.success
        print(f"\n{'Done' if outcome.success else 'Failed'}: {outcome.reason}")
        if answers:  # 答えを読みに行った（目的が成功した）ときだけ、答えの欄を出す。失敗したときは出さない
            if answers.candidates:  # 候補があるとき: 確からしい順に並べて表示する
                print("\nAnswer candidates, most likely first")
                print("  (confidence: probability in the final choice / in part: probability in its own part of the page)")
                for rank, c in enumerate(answers.candidates, 1):
                    print(f"  {rank}. {c.text}\n     confidence {c.confidence:.2f}, in part {c.in_part:.2f}{'  ' + c.url if c.url else ''}")
                    if c.subject:  # 最安・最多などで比べた候補には、その値が属するもの（商品名など）も添える
                        print(f"     of: {c.subject.text}  (confidence {c.subject.confidence:.2f}){'  ' + c.subject.url if c.subject.url else ''}")
            else:  # 答えを求める目的だが候補がない、または答えを求めない目的（「検索して」など）: 理由を出す
                print(f"\nNo answer: {answers.reason}")
        print(f"\n{usage.summary()}\nTrace: {trace.path}")
        if sys.stdin.isatty() and not args.headless:  # 端末で見ていて、ウィンドウがあるとき: 結果のページを確認できるよう、Enter までブラウザを閉じない
            await asyncio.to_thread(input, "Press Enter to close the browser...")
        return outcome.success


def _page_info(page: str) -> dict:
    """実行が終わったページの URL とタイトル。スナップショットの先頭から読む。"""
    url = re.search(r"- Page URL: (.*)", page)
    title = re.search(r"- Page Title: (.*)", page)
    return {"url": url[1].strip() if url else None, "title": title[1].strip() if title else None}


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
        sys.exit(f"error: {_flatten_group(group)[0]}\nTrace: {trace.path}")
    finally:
        trace.close()
    sys.exit(0 if ok else 1)
