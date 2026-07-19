"""test_macro_agent.py — 曲線倒掛 / 通膨 / 情緒缺席歸一化 / 模擬旗標。"""

from __future__ import annotations

import pytest

from multi_agent_system import MacroeconomicAgent, MacroReading


def _reading(spread, cpi, simulated=True):
    return MacroReading(
        yield_spread_pct=spread,
        cpi_yoy_pct=cpi,
        source="TEST",
        as_of="2026-07-19",
        is_simulated=simulated,
    )


def test_healthy_macro_high_score():
    v = MacroeconomicAgent().evaluate(_reading(1.2, 2.4), news_sentiment_mean=0.5)
    assert v.available
    # curve=0.8, cpi≈0.8667, sent=0.75 → 0.45*0.8+0.35*0.8667+0.2*0.75
    assert v.score == pytest.approx(0.8133, abs=1e-3)


def test_inversion_and_hot_cpi_low_score():
    v = MacroeconomicAgent().evaluate(_reading(-0.4, 5.6), news_sentiment_mean=-0.3)
    assert v.diagnostics["inverted"] is True
    assert v.diagnostics["cpi_hot"] is True
    # curve=0, cpi=0, sent=0.35 → 0.2*0.35
    assert v.score == pytest.approx(0.07, abs=1e-3)


def test_sentiment_missing_renormalizes():
    v = MacroeconomicAgent().evaluate(_reading(1.5, 2.0), news_sentiment_mean=None)
    assert v.diagnostics["sentiment_available"] is False
    # curve=1, cpi=1, 重新歸一化 (0.45+0.35) → 1.0
    assert v.score == pytest.approx(1.0, abs=1e-9)


def test_simulated_flag_surfaced_in_reason():
    v = MacroeconomicAgent().evaluate(_reading(1.0, 2.0, simulated=True), 0.0)
    assert v.diagnostics["is_simulated"] is True
    assert "模擬" in v.reason


def test_score_always_in_unit_interval():
    for spread in (-2.0, -0.1, 0.0, 0.5, 1.5, 3.0):
        for cpi in (0.0, 2.0, 3.5, 5.0, 9.0):
            v = MacroeconomicAgent().evaluate(_reading(spread, cpi), 0.1)
            assert 0.0 <= v.score <= 1.0
