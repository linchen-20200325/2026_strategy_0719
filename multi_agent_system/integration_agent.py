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
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Protocol

from config import NEWS_LOOKBACK_DAYS

from .allocation_agent import AssetAllocationAgent
from .contracts import Action, DataPacket, FinalDecision, PortfolioState
from .data_agent import DataAggregationAgent
from .macro_agent import MacroeconomicAgent
from .macro_providers import MacroDataProvider
from .strategy_agent import StrategyAgent
from .technical_agent import TechnicalAnalysisAgent

logger = logging.getLogger("multi_agent_system")


# ------------------------------------------------------------------ 券商介面

@dataclass(frozen=True)
class OrderReceipt:
    """下單回執。is_mock=True 代表未真實成交。"""

    order_id: str
    symbol: str
    side: str            # "BUY" / "SELL"
    quantity: float
    status: str
    is_mock: bool
    placed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BrokerAPI(Protocol):
    """券商下單契約。未來對接真實 API（永豐/IB/Alpaca…）時實作本介面即可。"""

    def place_order(self, symbol: str, side: str, quantity: float) -> OrderReceipt: ...


class MockBrokerAPI:
    """模擬券商：只記錄、不成交（安全 default）。

    ⚠️ 這是 Mock 介面。接真實券商前，任何情況都不會送出真實委託。
    """

    def __init__(self) -> None:
        self.blotter: list[OrderReceipt] = []

    def place_order(self, symbol: str, side: str, quantity: float) -> OrderReceipt:
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side 必須為 BUY/SELL，收到 {side}")
        if quantity <= 0:
            raise ValueError(f"quantity 必須為正，收到 {quantity}")
        receipt = OrderReceipt(
            order_id=f"MOCK-{uuid.uuid4().hex[:12]}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            status="SIMULATED_FILLED",
            is_mock=True,
        )
        self.blotter.append(receipt)
        logger.info("[MockBroker] %s %s x%s → %s", side, symbol, quantity, receipt.order_id)
        return receipt


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


@dataclass
class CycleResult:
    """單次工作流輸出（決策 + 資料封包 + 下單回執），供觀測。"""

    decision: FinalDecision
    packet: DataPacket
    receipt: OrderReceipt | None = None


# ------------------------------------------------------------------ 主編排

# 行動 → 委託方向；HOLD/abstain 不下單。
_ACTION_SIDE: dict[Action, str] = {
    Action.STRONG_BUY: "BUY",
    Action.ADD: "BUY",
    Action.REDUCE: "SELL",
    Action.STRONG_SELL: "SELL",
}
# 行動 → 下單數量佔 base_quantity 之比例（示意性 position sizing；
# 生產環境應改為波動度目標 / Kelly 等，屬 §8 未來需求，先不做）。
_ACTION_SIZE_FRACTION: dict[Action, float] = {
    Action.STRONG_BUY: 1.0,
    Action.ADD: 0.5,
    Action.REDUCE: 0.5,
    Action.STRONG_SELL: 1.0,
}


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

        # 2) 三位專家評估
        macro_reading = request.macro_provider.get_reading()
        macro_v = self.macro_agent.evaluate(macro_reading, packet.news_sentiment_mean)
        tech_v = self.technical_agent.evaluate(packet.technical)
        alloc_v = self.allocation_agent.evaluate(request.portfolio_state)

        # 3) 決策融合
        decision = self.strategy_agent.decide(
            request.tw_stock_id, macro_v, tech_v, alloc_v
        )

        # 4) 選配下單（Mock）
        receipt = None
        if request.auto_trade:
            receipt = self._maybe_trade(decision, request)

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

    # --------------------------------------------------------------- 下單
    def _maybe_trade(
        self, decision: FinalDecision, request: ResearchRequest
    ) -> OrderReceipt | None:
        if decision.abstained:
            logger.info("[Orchestrator] %s abstain，不下單", decision.tw_stock_id)
            return None
        side = _ACTION_SIDE.get(decision.action)
        if side is None:  # HOLD
            logger.info("[Orchestrator] %s 判定 Hold，不下單", decision.tw_stock_id)
            return None
        qty = request.base_quantity * _ACTION_SIZE_FRACTION[decision.action]
        if qty <= 0:
            return None
        return self.broker.place_order(request.tw_stock_id, side, qty)
