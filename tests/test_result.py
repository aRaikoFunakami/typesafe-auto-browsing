import json
from pathlib import Path

from typesafe_auto_browsing import _result_json, _save_snapshot
from typesafe_auto_browsing.trace import Trace

PAGE = "### Page\n- Page URL: https://x/\n- Page Title: T\n### Snapshot\n```yaml\n- generic [ref=e1]\n```"


def test_the_last_snapshot_is_saved_as_is_and_its_path_is_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    trace = Trace(Path("logs"))  # 相対パスの記録先でも、返すパスは絶対
    path = _save_snapshot(trace, PAGE)
    assert Path(path).is_absolute() and Path(path).read_text(encoding="utf-8") == PAGE
    assert _save_snapshot(trace, "") is None  # 読めたページがない（失敗）: ファイルはない
    trace.close()


def test_the_result_says_success_or_failure_and_where_the_page_is(tmp_path):
    trace = Trace(tmp_path)
    ok = json.loads(_result_json("g", True, "goal achieved", PAGE, "/x/final.yml", None, trace))
    assert ok["success"] is True and ok["page"] == {"url": "https://x/", "title": "T", "snapshot": "/x/final.yml"}
    crashed = json.loads(_result_json("g", False, "error: boom", "", None, None, trace))
    assert crashed["success"] is False and crashed["page"] == {"url": None, "title": None, "snapshot": None}
    trace.close()
