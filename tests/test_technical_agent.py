"""test_technical_agent.py — 超賣/超買、零寬度通道除零、RSI 超界、全無效。"""

from __future__ import annotations

import pytest

from multi_agent_system import TechnicalAnalysisAgent, TechnicalSnapshot


def _snap(close, rsi, upper, lower, stock_id="X"):
    return TechnicalSnapshot(
        stock_id=stock_id, as_of="2026-07-18", close=close,
        rsi=rsi, upper_band=upper, lower_band=lower,
    )


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
