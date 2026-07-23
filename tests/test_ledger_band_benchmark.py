"""test_ledger_band_benchmark.py — B：hit-band 隨 horizon 放大 + always-that-bucket 基準線。

修兩個策略體檢缺口：
* band bug —— 日尺度容差(0.5%)硬套月報酬(20 交易日) → 中性桶結構性 0% 命中。
  修正：對帳時 band × √horizon（隨機漫步 vol ∝ √時間）。
* 無基準 —— 命中率沒對照「無腦永遠喊該桶」的漂移 base rate → 漂移被讀成本事。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from config import (
    LEDGER_HIT_BAND_RATIO,
    REGIME_LABEL_BEAR,
    REGIME_LABEL_BULL,
    REGIME_LABEL_NEUTRAL,
    REGIME_UNTAGGED,
)
from multi_agent_system.ledger.reconcile import PriceBar, horizon_band
from multi_agent_system.ledger.report import build_report
from multi_agent_system.ledger.store import Judgment
from multi_agent_system.render_text import format_report


def _seq(start: str, n: int, fn) -> list[PriceBar]:
    d0 = date.fromisoformat(start)
    return [PriceBar(d0 + timedelta(days=i), float(fn(i))) for i in range(n)]


def _J(dstr, session, label, regime=REGIME_UNTAGGED):
    return Judgment(f"{dstr}T07:30:00+08:00", dstr, session, label, 0.5, regime)


# ───────────────────────────── horizon_band 純函式
def test_horizon_band_scales_by_sqrt():
    assert horizon_band(0.005, 20) == pytest.approx(0.005 * 20 ** 0.5)   # ≈ 2.24%
    assert horizon_band(0.005, 1) == pytest.approx(0.005)                # 1 日 → 不放大
    assert horizon_band(0.0, 5) == 0.0
    with pytest.raises(ValueError):
        horizon_band(-0.01, 5)          # 負容差 → Fail-Loud
    with pytest.raises(ValueError):
        horizon_band(0.005, 0)          # 非正 horizon → Fail-Loud


# ───────────────────────────── ★ band bug 修正 golden：中性桶不再結構性 0%
def test_neutral_bucket_no_longer_structurally_zero():
    # 一個月(20 交易日)只動 +1.5% —— 月尺度「幾乎沒動」，理應算中性命中。
    bars = _seq("2026-01-01", 25, lambda i: 100 + i * 0.075)   # 20 日 → +1.5%
    js = [_J("2026-01-01", "morning", REGIME_LABEL_NEUTRAL)]

    # 修正後：預設 band（None）→ 自動 ×√20 ≈ 2.24% → |1.5%| ≤ 2.24% → 命中
    rep_fixed = build_report(js, bars, horizon_n=20)
    assert rep_fixed.band == pytest.approx(LEDGER_HIT_BAND_RATIO * 20 ** 0.5)
    assert rep_fixed.buckets[REGIME_LABEL_NEUTRAL].hits == 1        # ✅ bug 已修

    # 對照舊 bug：顯式日尺度 band（不放大）→ |1.5%| > 0.5% → 未命中（結構性 0%）
    rep_bug = build_report(js, bars, horizon_n=20, band=0.005)
    assert rep_bug.buckets[REGIME_LABEL_NEUTRAL].hits == 0


# ───────────────────────────── always-that-bucket 基準線（漂移 ≠ 本事）
def test_base_rates_expose_drift():
    # 單調上漲盤：每筆對帳市場都「漲 > 容差」→ 市場走勢分布全落在「漲」。
    bars = _seq("2026-01-01", 30, lambda i: 100 + i)   # +5% / 5 日
    js = [
        _J("2026-01-01", "morning", REGIME_LABEL_BULL),
        _J("2026-01-02", "morning", REGIME_LABEL_BULL),
    ]
    rep = build_report(js, bars, horizon_n=5, band=0.005)
    # always-偏多 基準 = 100%（市場全漲）；always-中性/偏空 = 0%
    assert rep.base_rates[REGIME_LABEL_BULL] == pytest.approx(1.0)
    assert rep.base_rates[REGIME_LABEL_NEUTRAL] == 0.0
    assert rep.base_rates[REGIME_LABEL_BEAR] == 0.0
    # 偏多命中率 100%，但基準也 100% → 超額 0：這正是「常對但只是搭漂移便車」
    assert rep.buckets[REGIME_LABEL_BULL].hit_rate == pytest.approx(1.0)


def test_base_rates_partition_sums_to_one():
    bars = _seq("2026-01-01", 30, lambda i: 100 + (i % 3 - 1) * 2)   # 上下震盪
    js = [_J(f"2026-01-0{d}", "morning", REGIME_LABEL_NEUTRAL) for d in (1, 2, 3)]
    rep = build_report(js, bars, horizon_n=5, band=0.005)
    total = sum(v for v in rep.base_rates.values() if v is not None)
    assert total == pytest.approx(1.0)     # 漲/持平/跌 三態分割 → 和為 1


# ───────────────────────────── format 顯示容差 + 基準 + 超額
def test_format_shows_band_and_benchmark():
    bars = _seq("2026-01-01", 30, lambda i: 100 + i)
    js = [_J("2026-01-01", "morning", REGIME_LABEL_BULL),
          _J("2026-01-02", "morning", REGIME_LABEL_BULL)]
    txt = format_report(build_report(js, bars, horizon_n=5, band=0.005))
    assert "容差 ±" in txt                 # 標頭顯示有效容差
    assert "基準" in txt and "超額" in txt   # 每桶對照漂移基準
