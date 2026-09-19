import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError

from .agent import Settings, answer_goal, run_agent
from .answer import as_dicts  # not `answer`: that is the name of a submodule
from .playwright_mcp import playwright_session
from .selector import judge_tools
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
        "-t",
        "--threshold",
        type=float,
        default=0.5,
        help="With --dry-run, mark tools whose probability is at least this value (default: 0.5)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Only select the tools; do not operate the browser"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as one JSON object on stdout (progress goes to stderr); with --dry-run, the tool probabilities",
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
    """The goal in a prompt file: its lines without the comments (lines starting with #) and blank lines."""
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
    """Ask a person before a tool call that changes the page."""
    answer = await asyncio.to_thread(input, f"  confirm: {description}\n  proceed? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


async def _run(args: argparse.Namespace, goal: str, trace: Trace) -> bool:
    """Open the browser, then either judge the tools (--dry-run) or run the agent and read the answer."""
    async with playwright_session(args.headless) as session:
        tools = (await session.list_tools()).tools
        trace.event("mcp_tools", tools=[t.model_dump(mode="json") for t in tools])
        usage = Usage()
        async with AsyncTypeSafeClient() as typesafe:
            client = MeteredClient(typesafe, usage, trace)

            if args.dry_run:  # which tools TypeSafe expects the goal to need; a run offers every tool
                judgments = await judge_tools(client, goal, tools)
                selected = [j for j in judgments if j.probability >= args.threshold]
                trace.event(
                    "tools_selected",
                    selected=[j.tool.name for j in selected],
                    probabilities={j.tool.name: j.probability for j in judgments},
                )
                if args.json:
                    report = {
                        "goal": goal,
                        "threshold": args.threshold,
                        "selected": [j.tool.name for j in selected],
                        "probabilities": {j.tool.name: j.probability for j in judgments},
                        "usage": usage.as_dict(),
                        "trace": str(trace.path),
                    }
                    print(json.dumps(report, indent=2))
                    return True
                print(f"Goal: {goal}\nTrace: {trace.path}\n")
                for j in judgments:
                    mark = "*" if j.probability >= args.threshold else " "
                    print(f" {mark} {j.probability:5.2f}  {j.tool.name}")
                print(f"\nSelected {len(selected)}/{len(judgments)} tools (threshold {args.threshold})")
                print(f"\n{usage.summary()}")
                return True

            confirming = args.confirm
            out = sys.stderr if args.json else sys.stdout  # stdout is the JSON alone with --json
            print(f"Goal: {goal}\nTrace: {trace.path}\nConfirmation before each change: {'on' if confirming else 'off'}\n", file=out)

            def log(line: str) -> None:
                trace.event("log", line=line)
                print(line, file=out, flush=True)

            outcome = await run_agent(
                client,
                session,
                tools,
                goal,
                Settings(args.max_steps, args.done_threshold),
                log,
                _confirm if confirming else None,
            )
            answers = None
            if outcome.success:
                answers = await answer_goal(client, session, goal, outcome, log)
                trace.event("answers", wanted=answers.wanted, reason=answers.reason, candidates=as_dicts(answers))
        trace.event("outcome", success=outcome.success, reason=outcome.reason, usage=usage.as_dict())
        if args.json:
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
        if answers:
            if answers.candidates:
                print("\nAnswer candidates, most likely first")
                print("  (confidence: probability in the final choice / in part: probability in its own part of the page)")
                for rank, c in enumerate(answers.candidates, 1):
                    print(f"  {rank}. {c.text}\n     confidence {c.confidence:.2f}, in part {c.in_part:.2f}{'  ' + c.url if c.url else ''}")
                    if c.subject:
                        print(f"     of: {c.subject.text}  (confidence {c.subject.confidence:.2f}){'  ' + c.subject.url if c.subject.url else ''}")
            else:
                print(f"\nNo answer: {answers.reason}")
        print(f"\n{usage.summary()}\nTrace: {trace.path}")
        if sys.stdin.isatty() and not args.headless:
            await asyncio.to_thread(input, "Press Enter to close the browser...")
        return outcome.success


def _page_info(page: str) -> dict:
    """The URL and title of the page the run ended on, from the snapshot's header."""
    url = re.search(r"- Page URL: (.*)", page)
    title = re.search(r"- Page Title: (.*)", page)
    return {"url": url[1].strip() if url else None, "title": title[1].strip() if title else None}


def _flatten_group(error: BaseException) -> list[BaseException]:
    """The exceptions inside (possibly nested) exception groups."""
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
    """Entry point: read the goal, run, and exit 0 when the goal was achieved."""
    args = _parse_args()
    if args.confirm and not sys.stdin.isatty():
        sys.exit("error: --confirm asks on the terminal, but there is none")
    goal = _read_goal(args.goal, args.file)
    trace = Trace(args.log_dir)
    trace.event("run_start", goal=goal, args={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()})
    try:
        ok = asyncio.run(_run_traced(args, goal, trace))
    except* (TypeSafeError, RuntimeError) as group:  # raised inside MCP's task group
        sys.exit(f"error: {_flatten_group(group)[0]}\nTrace: {trace.path}")
    finally:
        trace.close()
    sys.exit(0 if ok else 1)
