"""実行の全記録。あとで調べるための、1 行に 1 つの JSON オブジェクト。

大きな文字列（ページのスナップショット）は、その隣のファイルに保存し、パスで参照する。

出入りするものは全て、起きたときに書く: TypeSafe へのリクエスト（state と質問）と応答、
Playwright MCP の呼び出しと出力の全文、実行が表示するログ行、エラー、結果。何も省略しない。
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
        # 記録にはページの内容と入力した文字列が入る: 所有者だけが読める
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
        """大きな文字列を、トレースファイルの隣に保存する。トレースからの相対パスを返す。"""
        directory = self.path.with_suffix("")
        directory.mkdir(mode=0o700, exist_ok=True)
        with os.fdopen(os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as file:
            file.write(text)
        return f"{directory.name}/{name}"

    def close(self) -> None:
        self._file.close()
