"""test_integration.py — 端到端工作流：多頭 / 風控減碼 / abstain / Mock 下單。"""

from __future__ import annotations

from datetime import date

from multi_agent_system import (
    MockBrokerAPI,
    ResearchRequest,
    SimulatedMacroProvider,
    WorkflowOrchestrator,
    default_portfolio_state,
)
from multi_agent_system.contracts import Action

AS_OF = date(2026, 7, 19)


def _orch(data_agent):
    return WorkflowOrchestrator(data_agent, broker=MockBrokerAPI())


def test_bullish_scenario_strong_buy_and_buys(data_agent):
    orch = _orch(data_agent)
    req = ResearchRequest(
        tw_stock_id="2330", us_stock_id="NVDA",
        news_keywords=["台積電", "半導體", "TSMC"],
        portfolio_state=default_portfolio_state(0.10, sharpe=1.4),
        macro_provider=SimulatedMacroProvider(yield_spread_pct=1.2, cpi_yoy_pct=2.4),
        as_of_date=AS_OF, auto_trade=True,
    )
    res = orch.run_once(req)
    assert res.decision.action == Action.STRONG_BUY
    assert res.decision.final_score >= 0.80
    assert res.receipt is not None and res.receipt.side == "BUY"
    assert res.receipt.is_mock is True


def test_risk_control_scenario_forces_sell(data_agent):
    orch = _orch(data_agent)
    req = ResearchRequest(
        tw_stock_id="2454", us_stock_id="AMD",
        news_keywords=["半導體"],
        portfolio_state=default_portfolio_state(0.35, sharpe=0.8),  # 超限
        macro_provider=SimulatedMacroProvider(yield_spread_pct=-0.4, cpi_yoy_pct=5.6),
        as_of_date=AS_OF, auto_trade=True,
    )
    res = orch.run_once(req)
    assert res.decision.risk_control_triggered is True
    assert res.decision.action in (Action.REDUCE, Action.STRONG_SELL)
    assert res.receipt is not None and res.receipt.side == "SELL"


def test_missing_stock_abstains_no_order(data_agent):
    orch = _orch(data_agent)
    req = ResearchRequest(
        tw_stock_id="9999", us_stock_id="NVDA",
        news_keywords=["台積電"],
        portfolio_state=default_portfolio_state(0.05, sharpe=1.0),
        macro_provider=SimulatedMacroProvider(yield_spread_pct=1.0, cpi_yoy_pct=2.0),
        as_of_date=AS_OF, auto_trade=True,
    )
    res = orch.run_once(req)
    assert res.decision.abstained is True
    assert res.decision.action == Action.HOLD
    assert res.receipt is None  # abstain 不下單


def test_auto_trade_off_places_no_order(data_agent):
    orch = _orch(data_agent)
    req = ResearchRequest(
        tw_stock_id="2330", us_stock_id="NVDA",
        news_keywords=["台積電"],
        portfolio_state=default_portfolio_state(0.10, sharpe=1.4),
        macro_provider=SimulatedMacroProvider(yield_spread_pct=1.2, cpi_yoy_pct=2.4),
        as_of_date=AS_OF, auto_trade=False,
    )
    res = orch.run_once(req)
    assert res.receipt is None


def test_run_scheduled_bounded_iterations(data_agent):
    orch = _orch(data_agent)
    req = ResearchRequest(
        tw_stock_id="2330", us_stock_id="NVDA",
        news_keywords=["台積電"],
        portfolio_state=default_portfolio_state(0.10, sharpe=1.4),
        macro_provider=SimulatedMacroProvider(yield_spread_pct=1.2, cpi_yoy_pct=2.4),
        as_of_date=AS_OF,
    )
    calls = []
    history = orch.run_scheduled(
        [req], interval_sec=0.0, max_iterations=3, _sleep=lambda s: calls.append(s)
    )
    assert len(history) == 3
    assert len(calls) == 2  # N-1 次 sleep


def test_batch_isolates_failures(data_agent):
    orch = _orch(data_agent)
    good = ResearchRequest(
        tw_stock_id="2330", us_stock_id="NVDA", news_keywords=["台積電"],
        portfolio_state=default_portfolio_state(0.10, sharpe=1.4),
        macro_provider=SimulatedMacroProvider(yield_spread_pct=1.2, cpi_yoy_pct=2.4),
        as_of_date=AS_OF,
    )
    # 這筆 portfolio 權重非法 → allocation raise ValueError，但不應拖垮整批
    bad = ResearchRequest(
        tw_stock_id="2330", us_stock_id="NVDA", news_keywords=["台積電"],
        portfolio_state=default_portfolio_state(1.5, sharpe=1.4),
        macro_provider=SimulatedMacroProvider(yield_spread_pct=1.2, cpi_yoy_pct=2.4),
        as_of_date=AS_OF,
    )
    results = orch.run_batch([good, bad])
    assert len(results) == 1  # 壞的被隔離，好的存活
