"""test_technical_agent.py — 超賣/超買、零寬度通道除零、RSI 超界、全無效。"""

from __future__ import annotations

import pytest

from multi_agent_system import TechnicalAnalysisAgent, TechnicalSnapshot
from multi_agent_system.technical_agent import (
    _chip_score,
    _kd_score,
    _ma_align_score,
)


def _snap(close, rsi, upper, lower, stock_id="X"):
    return TechnicalSnapshot(
        stock_id=stock_id, as_of="2026-07-18", close=close,
        rsi=rsi, upper_band=upper, lower_band=lower,
    )


def _ext(**kw):
    """加厚欄位（MA/KD/籌碼）齊全的 snapshot 建構子。"""
    base = dict(
        stock_id="2330", as_of="2026-07-18", close=100.0, rsi=50.0,
        upper_band=120.0, lower_band=80.0,
    )
    base.update(kw)
    return TechnicalSnapshot(**base)


def test_oversold_cheap_high_score():
    v = TechnicalAnalysisAgent().evaluate(_snap(927, 28, 1004, 924))
    assert v.available
    assert v.score > 0.9
    assert v.diagnostics["regime"] == "oversold_cheap"


def test_overbought_expensive_low_score():
    v = TechnicalAnalysisAgent().evaluate(_snap(1358, 76, 1366, 1186))
    assert v.score < 0.1
    assert v.diagnostics["regime"] == "overbought_expensive"


def test_zero_width_band_falls_back_to_rsi():
    # upper == lower：%B 除零 → 應改用 RSI 單指標且不炸
    v = TechnicalAnalysisAgent().evaluate(_snap(100, 50, 100, 100))
    assert v.available
    assert v.diagnostics["band_degenerate"] is True
    assert v.diagnostics["subcomponents_used"] == 1
    # RSI=50 → cheap_rsi=0.5
    assert v.score == pytest.approx(0.5, abs=1e-9)


def test_rsi_out_of_range_is_clamped_and_flagged():
    v = TechnicalAnalysisAgent().evaluate(_snap(100, 150, 120, 80))
    assert v.diagnostics["rsi_out_of_range"] is True
    assert v.diagnostics["rsi"] == 100.0  # clamp


def test_all_invalid_returns_unavailable():
    # close 無效 + RSI NaN + 通道退化 → 全數無效
    v = TechnicalAnalysisAgent().evaluate(_snap(float("nan"), float("nan"), 100, 100))
    assert not v.available
    assert v.score is None


def test_none_snapshot_unavailable():
    v = TechnicalAnalysisAgent().evaluate(None)
    assert not v.available


def test_price_breakout_above_band_expensive():
    # close 高於上軌 (%B>1) → cheapness_%B=0
    v = TechnicalAnalysisAgent().evaluate(_snap(130, 65, 120, 80))
    assert v.diagnostics["percent_b"] > 1.0
    assert v.score < 0.5


def test_score_in_unit_interval_grid():
    for rsi in (0, 15, 30, 50, 70, 85, 100):
        for close in (70, 80, 100, 120, 130):
            v = TechnicalAnalysisAgent().evaluate(_snap(close, rsi, 120, 80))
            assert 0.0 <= v.score <= 1.0


# ---------------------------------------------------------------- 加厚子分量（均線/KD/籌碼）
def test_ma_align_bull_neutral_bear():
    assert _ma_align_score(_ext(close=970, ma20=955, ma60=940)) == pytest.approx(1.0)   # 多頭排列
    assert _ma_align_score(_ext(close=930, ma20=955, ma60=977)) == pytest.approx(0.0)   # 空頭排列
    assert _ma_align_score(_ext(close=950, ma20=955, ma60=940)) == pytest.approx(2 / 3)  # close>MA60、MA20>MA60
    assert _ma_align_score(_ext()) is None                                              # 無 MA → None


def test_kd_golden_beats_death():
    golden = _kd_score(_ext(kd_k=60, kd_d=50))
    death = _kd_score(_ext(kd_k=40, kd_d=50))
    assert golden > death
    assert golden == pytest.approx(0.5 * 1 + 0.5 * (1 / 3))   # room=linear_map(60,80,20,0,1)=1/3
    assert _kd_score(_ext()) is None


def test_chip_buy_sell_saturate():
    assert _chip_score(_ext(total_net_lots=0.0)) == pytest.approx(0.5)
    assert _chip_score(_ext(total_net_lots=9000.0)) > 0.9
    assert _chip_score(_ext(total_net_lots=-9000.0)) < 0.1
    assert _chip_score(_ext()) is None


def test_backward_compat_only_bollinger_rsi():
    # 舊 stock.db（無 MA/KD/籌碼）→ 只用 %B + RSI，歸一化後 = 原本 0.5/0.5。
    v = TechnicalAnalysisAgent().evaluate(_ext(rsi=30.0))   # %B=0.5→cheap 0.5；RSI30→cheap 1.0
    assert v.diagnostics["subcomponents_used"] == 2
    assert v.score == pytest.approx(0.75)


def test_enriched_uses_all_five():
    v = TechnicalAnalysisAgent().evaluate(_ext(
        rsi=30.0, ma20=95.0, ma60=90.0, kd_k=60.0, kd_d=50.0, total_net_lots=9000.0,
    ))
    assert v.diagnostics["subcomponents_used"] == 5
    assert {"ma_align", "kd", "chip"} <= set(v.diagnostics)


def test_downtrend_and_selling_moderate_oversold():
    # 超賣但空頭排列 + 死叉 + 賣超 → 加厚後應低於純便宜舊分（多因子修正 falling knife）。
    cheap_only = TechnicalAnalysisAgent().evaluate(_ext(close=82.0, rsi=25.0)).score
    cheap_but_weak = TechnicalAnalysisAgent().evaluate(_ext(
        close=82.0, rsi=25.0, ma20=95.0, ma60=100.0, kd_k=20.0, kd_d=30.0, total_net_lots=-5000.0,
    )).score
    assert cheap_but_weak < cheap_only
