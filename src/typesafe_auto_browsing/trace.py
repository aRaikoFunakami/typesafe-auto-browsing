"""Full record of a run, one JSON object per line, for investigating it afterwards.

Large texts (page snapshots) are stored as files next to it and referenced by path.

Everything that goes in or out is written as it happens: the TypeSafe requests (state and
questions) and responses, Playwright MCP calls and their complete output, the log lines the
run prints, errors and the outcome. Nothing is shortened.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return repr(value)


class Trace:
    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl"
        # The record holds page contents and typed text: readable by the owner only.
        self._file = os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8", buffering=1)
        self._start = time.monotonic()
        self._seq = 0

    @property
    def next_seq(self) -> int:
        return self._seq + 1

    def event(self, kind: str, **data: Any) -> None:
        self._seq += 1
        record = {
            "seq": self._seq,
            "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "elapsed_s": round(time.monotonic() - self._start, 3),
            "kind": kind,
            **data,
        }
        self._file.write(json.dumps(record, ensure_ascii=False, default=_default) + "\n")

    def attach(self, name: str, text: str) -> str:
        """Store a large text next to the trace file; returns its path relative to the trace."""
        directory = self.path.with_suffix("")
        directory.mkdir(mode=0o700, exist_ok=True)
        with os.fdopen(os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as file:
            file.write(text)
        return f"{directory.name}/{name}"

    def close(self) -> None:
        self._file.close()
