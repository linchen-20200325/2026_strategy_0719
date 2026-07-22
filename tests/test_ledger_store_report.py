"""test_ledger_store_report.py — ledger persist / record / 聚合報表。

store: append + read round-trip、Fail-Loud 損毀列、檔不存在。
recorder: 存一筆、record 失敗不 raise（回 None）。
report: 去重、方向命中率、per-bucket、pending 計數、小樣本旗標。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from config import REGIME_LABEL_BEAR, REGIME_LABEL_BULL, REGIME_LABEL_NEUTRAL
from multi_agent_system.ledger.reconcile import PriceBar
from multi_agent_system.ledger.recorder import record_market_regime
from multi_agent_system.ledger.report import build_report, dedup_judgments, format_report
from multi_agent_system.ledger.store import Judgment, append_judgment, read_judgments

TW = timezone(timedelta(hours=8))


# ------------------------------------------------------------------ store
def test_store_append_read_roundtrip(tmp_path):
    p = str(tmp_path / "ledger.jsonl")
    j1 = Judgment("2026-07-22T07:30:00+08:00", "2026-07-22", "morning", REGIME_LABEL_BULL, 0.7)
    j2 = Judgment("2026-07-22T16:30:00+08:00", "2026-07-22", "afternoon", REGIME_LABEL_BEAR, 0.3)
    append_judgment(j1, path=p)
    append_judgment(j2, path=p)
    got = read_judgments(path=p)
    assert got == [j1, j2]                      # 保序、值一致


def test_store_read_missing_file_returns_empty(tmp_path):
    assert read_judgments(path=str(tmp_path / "nope.jsonl")) == []


def test_store_corrupt_line_raises(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text('{"judged_at":"x"}\nNOT_JSON\n', encoding="utf-8")   # 第2列壞
    with pytest.raises(ValueError):
        read_judgments(path=str(p))


def test_store_blank_lines_skipped(tmp_path):
    p = tmp_path / "ledger.jsonl"
    j = Judgment("2026-07-22T07:30:00+08:00", "2026-07-22", "morning", REGIME_LABEL_NEUTRAL, 0.5)
    append_judgment(j, path=str(p))
    p.write_text(p.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert read_judgments(path=str(p)) == [j]


# ------------------------------------------------------------------ recorder
def test_record_market_regime_writes_row(tmp_path):
    p = str(tmp_path / "ledger.jsonl")
    when = datetime(2026, 7, 22, 7, 30, tzinfo=TW)
    j = record_market_regime(label=REGIME_LABEL_BEAR, overall=0.401234,
                             session="morning", when=when, path=p)
    assert j is not None
    assert j.judged_date == "2026-07-22" and j.session == "morning"
    assert j.overall == pytest.approx(0.401234)     # round(6)
    assert read_judgments(path=p) == [j]


def test_record_failure_returns_none_not_raise(tmp_path):
    # 寫入不可寫路徑（目錄當檔名）→ record 不可 raise（不擋推播），回 None。
    bad = str(tmp_path / "adir")
    import os
    os.makedirs(bad)
    out = record_market_regime(label=REGIME_LABEL_BULL, overall=0.7,
                               session="morning", when=datetime(2026, 7, 22, tzinfo=TW), path=bad)
    assert out is None                              # loud log + fail token，不炸


# ------------------------------------------------------------------ report
def _seq(start_iso: str, n: int, price_fn) -> list[PriceBar]:
    d0 = date.fromisoformat(start_iso)
    return [PriceBar(d0 + timedelta(days=i), float(price_fn(i))) for i in range(n)]


def _J(dstr, session, label, overall=0.5):
    return Judgment(f"{dstr}T07:30:00+08:00", dstr, session, label, overall)


def test_dedup_keeps_last_per_date_session():
    a = _J("2026-01-01", "morning", REGIME_LABEL_BULL)
    b = _J("2026-01-01", "morning", REGIME_LABEL_BEAR)   # 同日同 session 重跑
    c = _J("2026-01-01", "afternoon", REGIME_LABEL_BULL)
    out = dedup_judgments([a, b, c])
    assert out == [b, c]                               # morning 取最後(b)，afternoon 保留


def test_build_report_hit_rate_and_buckets():
    # 30 個交易日，價從 100 每日 +1（單調上漲）。horizon=5、band=0.5%。
    bars = _seq("2026-01-01", 30, lambda i: 100 + i)
    # 都在漲：偏多→命中、偏空→未命中。
    js = [
        _J("2026-01-01", "morning", REGIME_LABEL_BULL),    # entry100→exit105 +5% 命中
        _J("2026-01-02", "morning", REGIME_LABEL_BEAR),    # 漲 → 偏空未命中
        _J("2026-01-03", "morning", REGIME_LABEL_BULL),    # 命中
    ]
    rep = build_report(js, bars, horizon_n=5, band=0.005)
    assert rep.n_total == 3 and rep.n_scored == 3 and rep.n_pending == 0
    assert rep.buckets[REGIME_LABEL_BULL].n == 2 and rep.buckets[REGIME_LABEL_BULL].hits == 2
    assert rep.buckets[REGIME_LABEL_BEAR].n == 1 and rep.buckets[REGIME_LABEL_BEAR].hits == 0
    assert rep.directional_n == 3 and rep.directional_hit_rate == pytest.approx(2 / 3)
    assert rep.buckets[REGIME_LABEL_BULL].avg_forward_return > 0


def test_build_report_counts_pending_when_not_matured():
    bars = _seq("2026-01-01", 3, lambda i: 100 + i)      # 只有 3 天
    js = [_J("2026-01-01", "morning", REGIME_LABEL_BULL)]
    rep = build_report(js, bars, horizon_n=20, band=0.005)  # 要 T+20 → 未到期
    assert rep.n_scored == 0 and rep.n_pending == 1
    assert rep.directional_hit_rate is None


def test_format_report_small_sample_flag():
    bars = _seq("2026-01-01", 30, lambda i: 100 + i)
    js = [_J("2026-01-01", "morning", REGIME_LABEL_BULL)]
    txt = format_report(build_report(js, bars, horizon_n=5, band=0.005))
    assert "判讀對帳" in txt and "T+5" in txt
    assert "樣本少，僅供參考" in txt                   # n=1 < 30 → 旗標


# ------------------------------------------------------------------ CLI 記錄整合
def test_cli_market_digest_record_writes_ledger(tmp_path, monkeypatch):
    # --record 在 broadcast 跑完後把大盤判讀 append 進 LEDGER_FILE（demo + dry-run，不需 token）。
    monkeypatch.setenv("LEDGER_FILE", str(tmp_path / "ledger.jsonl"))
    import run_pipeline

    rc = run_pipeline.main(
        ["--session", "morning", "--demo", "--market-digest", "--record", "--dry-run"]
    )
    assert rc == 0
    js = read_judgments(path=str(tmp_path / "ledger.jsonl"))
    assert len(js) == 1
    assert js[0].session == "morning"
    assert js[0].label in (REGIME_LABEL_BULL, REGIME_LABEL_NEUTRAL, REGIME_LABEL_BEAR)
    assert 0.0 <= js[0].overall <= 1.0
