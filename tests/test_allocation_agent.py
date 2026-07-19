"""test_allocation_agent.py — Sharpe 映射、集中度風控、returns 自算、邊界。"""

from __future__ import annotations

import pytest

from multi_agent_system import AssetAllocationAgent, PortfolioState, default_portfolio_state


def test_normal_weight_uses_sharpe_score():
    v = AssetAllocationAgent().evaluate(default_portfolio_state(0.10, sharpe=1.4))
    assert v.available
    assert v.diagnostics["risk_control_triggered"] is False
    # sharpe_score = 1.4/2 = 0.7
    assert v.score == pytest.approx(0.7)


def test_concentration_breach_forces_low_score():
    v = AssetAllocationAgent().evaluate(default_portfolio_state(0.35, sharpe=1.9))
    assert v.diagnostics["risk_control_triggered"] is True
    # overshoot=(0.35-0.20)/0.20=0.75 → 0.25*(1-0.75)=0.0625
    assert v.score == pytest.approx(0.0625)
    assert "🚨" in v.reason


def test_breach_score_never_exceeds_cap():
    # 即使 Sharpe 爆表，一旦超限，分數必 <= RISK_CONTROL_SCORE_CAP
    v = AssetAllocationAgent().evaluate(default_portfolio_state(0.21, sharpe=5.0))
    assert v.score <= 0.25


def test_sharpe_from_returns():
    returns = tuple([0.001, 0.003, -0.001, 0.002, 0.0])
    state = PortfolioState(0.05, 0.20, returns=returns)
    v = AssetAllocationAgent().evaluate(state)
    assert v.available
    assert v.diagnostics["sharpe_source"] == "computed_from_returns"


def test_zero_vol_returns_unavailable():
    state = PortfolioState(0.05, 0.20, returns=(0.01, 0.01, 0.01))
    v = AssetAllocationAgent().evaluate(state)
    assert not v.available
    assert "Sharpe" in v.reason


def test_no_sharpe_no_returns_unavailable():
    v = AssetAllocationAgent().evaluate(PortfolioState(0.05, 0.20))
    assert not v.available


def test_invalid_weight_raises():
    with pytest.raises(ValueError):
        AssetAllocationAgent().evaluate(PortfolioState(1.5, 0.20, sharpe=1.0))
    with pytest.raises(ValueError):
        AssetAllocationAgent().evaluate(PortfolioState(0.1, 0.0, sharpe=1.0))


def test_weight_equal_cap_is_not_breach():
    # 嚴格大於才算超限；等於上限不觸發
    v = AssetAllocationAgent().evaluate(PortfolioState(0.20, 0.20, sharpe=1.0))
    assert v.diagnostics["risk_control_triggered"] is False
