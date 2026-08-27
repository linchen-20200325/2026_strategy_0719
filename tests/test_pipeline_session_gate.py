"""test_pipeline_session_gate.py — run_pipeline 端對端：場次判定 + 遲到閘。

守住兩件單元測試抓不到的事：
1. 遲到閘**擺對位置** —— 必須早於 orchestrator.run_batch / record_*。
   若只擋在 LinePusher 前面，判讀已經落帳，會留下「沒推出去卻已記帳」的幽靈紀錄。
   故本檔同時斷言「沒推」**且**「ledger 沒有新增列」。
2. 平台延遲不是本系統故障 → 退出碼 0（不讓排程變紅 X）；判不出場次才非零。

時間注入走 monkeypatch `run_pipeline._now_utc`（不用 freezegun —— 不在 requirements）。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

import run_pipeline

MORNING_CRON = "30 23 * * 0-4"
INCIDENT_NOW = datetime(2026, 8, 27, 4, 39, tzinfo=timezone.utc)   # 盤前班遲到 5h09m
ON_TIME_NOW = datetime(2026, 8, 26, 23, 35, tzinfo=timezone.utc)   # 同一班，只遲 5 分


class _RecordingPusher:
    """替身 LinePusher：只記錄有沒有被呼叫，絕不連外。"""

    calls: list[str] = []

    def __init__(self, *_args, **_kwargs):
        pass

    def push_text(self, text: str) -> None:
        _RecordingPusher.calls.append(text)


@pytest.fixture
def gate_env(tmp_path, monkeypatch):
    """把 ledger 導到 tmp、給假 token、清乾淨場次來源環境變數。"""
    _RecordingPusher.calls = []
    ledger = tmp_path / "ledger.jsonl"
    stock_ledger = tmp_path / "stock_ledger.jsonl"
    monkeypatch.setenv("LEDGER_FILE", str(ledger))
    monkeypatch.setenv("STOCK_LEDGER_FILE", str(stock_ledger))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy-token-for-test")
    monkeypatch.delenv("GITHUB_EVENT_SCHEDULE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(run_pipeline, "LinePusher", _RecordingPusher)
    return {"ledger": ledger, "stock_ledger": stock_ledger}


def _lines(path) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as fh:
        return sum(1 for ln in fh if ln.strip())


def test_late_scheduled_run_neither_pushes_nor_records(gate_env, monkeypatch):
    """★ 事故情境：盤前班遲到 5h09m → 退出碼 0、沒推播、ledger 零新增列。"""
    monkeypatch.setenv("GITHUB_EVENT_SCHEDULE", MORNING_CRON)
    monkeypatch.setattr(run_pipeline, "_now_utc", lambda: INCIDENT_NOW)

    rc = run_pipeline.main(["--demo", "--market-digest", "--record"])

    assert rc == 0                                   # 平台延遲不是系統故障
    assert _RecordingPusher.calls == []              # 沒推播
    assert _lines(gate_env["ledger"]) == 0           # 沒落帳（閘在 record 之前）
    assert _lines(gate_env["stock_ledger"]) == 0


def test_on_time_scheduled_run_pushes_and_records(gate_env, monkeypatch):
    """對照組：同一條 cron 只遲 5 分 → 照常推播 + 落帳，且標籤是「早盤前」。"""
    monkeypatch.setenv("GITHUB_EVENT_SCHEDULE", MORNING_CRON)
    monkeypatch.setattr(run_pipeline, "_now_utc", lambda: ON_TIME_NOW)

    rc = run_pipeline.main(["--demo", "--market-digest", "--record"])

    assert rc == 0
    assert len(_RecordingPusher.calls) == 1
    assert "早盤前" in _RecordingPusher.calls[0]     # 場次來自 cron，非時鐘反推
    assert _lines(gate_env["ledger"]) == 1


def test_missing_session_and_cron_exits_nonzero(gate_env, monkeypatch):
    """既無 --session 也無 cron → Fail-Loud 非零退出，且不推播。"""
    monkeypatch.setattr(run_pipeline, "_now_utc", lambda: ON_TIME_NOW)

    rc = run_pipeline.main(["--demo", "--market-digest", "--record"])

    assert rc != 0
    assert _RecordingPusher.calls == []
    assert _lines(gate_env["ledger"]) == 0


def test_unregistered_cron_exits_nonzero(gate_env, monkeypatch):
    """cron 未登錄 config.CRON_SESSIONS → 非零退出，不猜場次。"""
    monkeypatch.setenv("GITHUB_EVENT_SCHEDULE", "0 12 * * *")
    monkeypatch.setattr(run_pipeline, "_now_utc", lambda: ON_TIME_NOW)

    rc = run_pipeline.main(["--demo", "--market-digest", "--record"])

    assert rc != 0
    assert _RecordingPusher.calls == []


def test_explicit_session_is_not_gated(gate_env, monkeypatch):
    """明示 --session（NAS crontab / 手動補推）不受遲到閘影響 —— 沒有排定時刻可比對。"""
    monkeypatch.setenv("GITHUB_EVENT_SCHEDULE", MORNING_CRON)
    monkeypatch.setattr(run_pipeline, "_now_utc", lambda: INCIDENT_NOW)

    rc = run_pipeline.main(["--session", "afternoon", "--demo", "--market-digest", "--record"])

    assert rc == 0
    assert len(_RecordingPusher.calls) == 1
    assert "收盤後" in _RecordingPusher.calls[0]


def test_late_run_emits_github_warning_annotation(gate_env, monkeypatch, capsys):
    """在 Actions 內遲到 → 印 `::warning::`（退出碼仍 0，只是留黃字）。"""
    monkeypatch.setenv("GITHUB_EVENT_SCHEDULE", MORNING_CRON)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(run_pipeline, "_now_utc", lambda: INCIDENT_NOW)

    rc = run_pipeline.main(["--demo", "--market-digest", "--record"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "::warning::" in out and "309 分鐘" in out
