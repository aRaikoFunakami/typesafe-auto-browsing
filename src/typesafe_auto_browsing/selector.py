from dataclasses import dataclass

from mcp.types import Tool
from typesafe_sdk import Noul

from .usage import MeteredClient


@dataclass(frozen=True)
class ToolJudgment:
    tool: Tool
    probability: float


def _question(name: str) -> Noul:
    return Noul(
        instructions=(
            "To achieve `goal` in a Chrome browser, would the browser tool "
            f"`tools.{name}` be called at least once?"
        ),
        criteria={
            "true": (
                "The tool performs an action or observation that this goal needs, "
                "including the page inspection required to locate elements "
                "before interacting with them."
            ),
            "false": (
                "The goal can be achieved without this tool; it only helps with "
                "unrelated situations."
            ),
        },
    )


async def judge_tools(
    client: MeteredClient, goal: str, tools: list[Tool]
) -> list[ToolJudgment]:
    """Ask one independent yes/no question per tool, all over the same state.

    The questions are evaluated in parallel by a single request; the caller
    decides which probabilities are high enough to select a tool.
    """
    state = {"goal": goal, "tools": {t.name: t.description for t in tools}}
    response = await client.system_one(state, {t.name: _question(t.name) for t in tools})
    judgments = [ToolJudgment(t, response.nouls[t.name].noul) for t in tools]
    return sorted(judgments, key=lambda j: j.probability, reverse=True)
