"""Token and cost accounting for the TypeSafe requests of a run."""

import time
from dataclasses import dataclass

from typesafe_sdk import AsyncTypeSafeClient

from .trace import Trace

# https://docs.typesafe.ai/models : Jev 1.13 costs $42 per billion input tokens; output is free.
TYPESAFE_INPUT_USD_PER_TOKEN = 42 / 1_000_000_000


@dataclass
class Usage:
    typesafe_requests: int = 0
    typesafe_input_tokens: int = 0
    typesafe_output_tokens: int = 0

    @property
    def typesafe_cost_usd(self) -> float:
        return self.typesafe_input_tokens * TYPESAFE_INPUT_USD_PER_TOKEN

    def as_dict(self) -> dict:
        return {
            "requests": self.typesafe_requests,
            "input_tokens": self.typesafe_input_tokens,
            "output_tokens": self.typesafe_output_tokens,
            "cost_usd": self.typesafe_cost_usd,
        }

    def summary(self) -> str:
        return (
            f"Usage: TypeSafe {self.typesafe_requests} requests, "
            f"{self.typesafe_input_tokens:,} input / {self.typesafe_output_tokens:,} output tokens"
            f"  ${self.typesafe_cost_usd:.4f}"
        )


class MeteredClient:
    """An AsyncTypeSafeClient that records the token usage and the full text of every request."""

    def __init__(self, client: AsyncTypeSafeClient, usage: Usage, trace: Trace):
        self._client = client
        self.usage = usage
        self.trace = trace

    async def system_one(self, state, questions, **kwargs):
        self.trace.event("typesafe_request", state=state, questions=questions, options=kwargs)
        started = time.monotonic()
        try:
            response = await self._client.system_one(state, questions, **kwargs)
        except Exception as error:
            self.trace.event(
                "typesafe_error",
                error_type=type(error).__name__,
                message=str(error),
                duration_s=round(time.monotonic() - started, 3),
            )
            raise
        self.usage.typesafe_requests += 1
        self.usage.typesafe_input_tokens += response.usage.input_tokens or 0
        self.usage.typesafe_output_tokens += response.usage.output_tokens or 0
        self.trace.event(
            "typesafe_response", response=response, duration_s=round(time.monotonic() - started, 3)
        )
        return response
