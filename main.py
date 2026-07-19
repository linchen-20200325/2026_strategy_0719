"""main.py — 多智能體投研系統 Demo 入口（可直接 `python main.py` 跑通）。

流程：seed 三個 demo DB → 建立 orchestrator → 跑三個代表性情境 → 印出決策與診斷。
情境設計涵蓋：多頭訊號、集中度風控強制減碼、資料不足 abstain。
"""

from __future__ import annotations

import logging

# 觀察基準日：對齊 demo 資料的新聞視窗。
from datetime import date

from multi_agent_system import (
    DataAggregationAgent,
    MockBrokerAPI,
    ResearchRequest,
    SimulatedMacroProvider,
    WorkflowOrchestrator,
    default_portfolio_state,
)
from scripts.seed_demo_dbs import default_demo_dir, seed_all

AS_OF = date(2026, 7, 19)


def _print_result(title: str, result) -> None:
    d = result.decision
    print("\n" + "=" * 78)
    print(f"情境：{title}")
    print("-" * 78)
    print(d.summary())
    print(d.rationale)
    if result.packet.warnings:
        print("  資料告警：")
        for w in result.packet.warnings:
            print(f"    • {w}")
    if result.receipt:
        r = result.receipt
        print(f"  下單(Mock)：{r.side} {r.symbol} x{r.quantity} → {r.order_id} [{r.status}]")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    paths = seed_all(default_demo_dir())
    print("已建立 DEMO 資料庫：", paths)

    agent = DataAggregationAgent(paths["stock_db"], paths["fund_db"], paths["news_db"])
    broker = MockBrokerAPI()
    orch = WorkflowOrchestrator(agent, broker=broker)

    # --- 情境 1：2330 超賣 + 總經健康 + 權重正常 → 偏多 ---
    req1 = ResearchRequest(
        tw_stock_id="2330",
        us_stock_id="NVDA",
        news_keywords=["台積電", "半導體", "TSMC"],
        portfolio_state=default_portfolio_state(current_weight_ratio=0.10, sharpe=1.4),
        macro_provider=SimulatedMacroProvider(
            yield_spread_pct=1.2, cpi_yoy_pct=2.4, scenario="healthy"
        ),
        as_of_date=AS_OF,
        auto_trade=True,
    )
    _print_result("2330 超賣 + 健康總經 + 權重 10%（正常）", orch.run_once(req1))

    # --- 情境 2：2454 超買 + 曲線倒掛 + 集中度超限 → 風控強制減碼 ---
    req2 = ResearchRequest(
        tw_stock_id="2454",
        us_stock_id="AMD",
        news_keywords=["半導體"],
        portfolio_state=default_portfolio_state(current_weight_ratio=0.35, sharpe=0.8),
        macro_provider=SimulatedMacroProvider(
            yield_spread_pct=-0.4, cpi_yoy_pct=5.6, scenario="stagflation"
        ),
        as_of_date=AS_OF,
        auto_trade=True,
    )
    _print_result("2454 超買 + 倒掛/高通膨 + 權重 35%（超限 20%）", orch.run_once(req2))

    # --- 情境 3：查無此股 → 技術面缺料 → abstain（不臆造）---
    req3 = ResearchRequest(
        tw_stock_id="9999",
        us_stock_id="NVDA",
        news_keywords=["台積電"],
        portfolio_state=default_portfolio_state(current_weight_ratio=0.05, sharpe=1.0),
        macro_provider=SimulatedMacroProvider(
            yield_spread_pct=1.0, cpi_yoy_pct=2.0, scenario="healthy"
        ),
        as_of_date=AS_OF,
        auto_trade=True,
    )
    _print_result("9999 資料庫查無 → 資料不足 abstain", orch.run_once(req3))

    print("\n" + "=" * 78)
    print(f"Mock 交易簿共 {len(broker.blotter)} 筆委託（皆未真實成交）。")


if __name__ == "__main__":
    main()
