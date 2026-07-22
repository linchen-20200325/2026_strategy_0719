"""integration_agent.py — 程式整合專家 (System Integration Agent)。

單一職責
--------
建立主工作流 (Workflow Orchestrator)：把資料代理人 → 三位專家 → 策略融合串起來，
支援「定點觸發」(run_once / run_batch) 與「定時觸發」(run_scheduled 骨架)，
並保留對接真實券商 API 的 Mock 下單介面（本檔絕不真實下單）。

資料流向
--------
    ResearchRequest
        │
        ▼
    DataAggregationAgent ──► DataPacket ──┬─► TechnicalAgent   ─┐
                                          │                     │
    MacroDataProvider ────► MacroReading ─┴─► MacroAgent  ──────┤
                                                                ├─► StrategyAgent ─► FinalDecision
    PortfolioState ─────────────────────────► AllocationAgent ─┘                        │
                                                                                        ▼
                                                          (auto_trade) BrokerAPI.place_order() [MOCK]

失敗降級
--------
* 資料層 DataSourceError 向上拋（Fail Loud）；策略層對缺料 abstain。
* 下單一律走 BrokerAPI 介面；預設 MockBrokerAPI 只記錄不成交，is_mock=True。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from config import NEWS_LOOKBACK_DAYS

from .allocation_agent import AssetAllocationAgent
from .broker import BrokerAPI, MockBrokerAPI, maybe_trade
from .contracts import (
    CycleResult,
    PortfolioState,
)
from .data_agent import DataAggregationAgent
from .fundamental_agent import FundamentalAgent
from .macro_agent import MacroeconomicAgent
from .macro_providers import MacroDataProvider
from .strategy_agent import StrategyAgent
from .technical_agent import TechnicalAnalysisAgent

logger = logging.getLogger("multi_agent_system")


# ------------------------------------------------------------------ 請求/結果

@dataclass(frozen=True)
class ResearchRequest:
    """一次投研請求。"""

    tw_stock_id: str
    us_stock_id: str
    news_keywords: Sequence[str]
    portfolio_state: PortfolioState
    macro_provider: MacroDataProvider
    lookback_days: int = NEWS_LOOKBACK_DAYS
    as_of_date: date | None = None
    auto_trade: bool = False           # 預設不下單（即使是 Mock 也要顯式開啟）
    base_quantity: float = 1000.0      # 下單基準數量（張/股，示意）


# ------------------------------------------------------------------ 主編排

class WorkflowOrchestrator:
    """主工作流編排器。"""

    def __init__(
        self,
        data_agent: DataAggregationAgent,
        *,
        broker: BrokerAPI | None = None,
        require_all_experts: bool = True,
    ) -> None:
        self.data_agent = data_agent
        self.macro_agent = MacroeconomicAgent()
        self.technical_agent = TechnicalAnalysisAgent()
        self.fundamental_agent = FundamentalAgent()
        self.allocation_agent = AssetAllocationAgent()
        self.strategy_agent = StrategyAgent(require_all_experts=require_all_experts)
        self.broker = broker or MockBrokerAPI()

    # ----------------------------------------------------------- 定點觸發
    def run_once(self, request: ResearchRequest) -> CycleResult:
        """跑完整一輪投研流程並（選配）下單。"""
        # 1) 跨庫抓資料
        packet = self.data_agent.aggregate(
            request.tw_stock_id,
            request.us_stock_id,
            request.news_keywords,
            lookback_days=request.lookback_days,
            as_of_date=request.as_of_date,
        )
        if packet.warnings:
            for w in packet.warnings:
                logger.warning("[DataAgent] %s", w)

        # 2) 專家評估（總經 / 技術 / 基本面 / 配置）
        macro_reading = request.macro_provider.get_reading()
        macro_v = self.macro_agent.evaluate(macro_reading, packet.news_sentiment_mean)
        tech_v = self.technical_agent.evaluate(packet.technical)
        fund_v = self.fundamental_agent.evaluate(packet.financials, packet.revenue_yoy_pct)
        alloc_v = self.allocation_agent.evaluate(request.portfolio_state)

        # 3) 決策融合（基本面為選填專家，缺 → 退回三專家歸一化）
        decision = self.strategy_agent.decide(
            request.tw_stock_id, macro_v, tech_v, alloc_v, fund_v
        )

        # 4) 選配下單（Mock）
        receipt = None
        if request.auto_trade:
            receipt = maybe_trade(self.broker, decision, base_quantity=request.base_quantity)

        return CycleResult(decision=decision, packet=packet, receipt=receipt)

    def run_batch(self, requests: Sequence[ResearchRequest]) -> list[CycleResult]:
        """對一籃子標的依序跑投研（點觸發批次）。"""
        results: list[CycleResult] = []
        for req in requests:
            try:
                results.append(self.run_once(req))
            except Exception:  # noqa: BLE001 - 單一標的失敗不拖垮整批，但要記錄
                logger.exception("[Orchestrator] 標的 %s 投研失敗", req.tw_stock_id)
        return results

    # ----------------------------------------------------------- 定時觸發
    def run_scheduled(
        self,
        requests: Sequence[ResearchRequest],
        *,
        interval_sec: float,
        max_iterations: int,
        _sleep=time.sleep,
    ) -> list[list[CycleResult]]:
        """定時觸發骨架（簡易輪詢）。

        生產環境建議改用 cron / APScheduler / Airflow，以取得重試、錯過補跑、
        監控告警等能力。此處提供有界迴圈（max_iterations 防止無限跑），
        並允許注入 _sleep 以利測試。
        """
        if interval_sec < 0:
            raise ValueError("interval_sec 不可為負")
        if max_iterations <= 0:
            raise ValueError("max_iterations 必須為正")
        history: list[list[CycleResult]] = []
        for i in range(max_iterations):
            logger.info("[Scheduler] 第 %d/%d 輪", i + 1, max_iterations)
            history.append(self.run_batch(requests))
            if i < max_iterations - 1:
                _sleep(interval_sec)
        return history
