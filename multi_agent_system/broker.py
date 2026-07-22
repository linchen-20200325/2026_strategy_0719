"""broker.py — 券商下單介面 + Mock 實作 + 依決策的部位 sizing。L3 execution。

從 integration_agent 拆出「執行」關注點：下單契約 / Mock 券商 / 行動→方向+數量映射 /
maybe_trade（依決策下單）。編排器（WorkflowOrchestrator）只留「編排」，執行細節在此。
"""

from __future__ import annotations

import logging
import uuid
from typing import Protocol

from .contracts import Action, FinalDecision, OrderReceipt

logger = logging.getLogger("multi_agent_system")


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


def maybe_trade(
    broker: BrokerAPI, decision: FinalDecision, *, base_quantity: float
) -> OrderReceipt | None:
    """依決策下單（呼叫端負責判斷 auto_trade 是否啟用）。

    abstain / HOLD / qty<=0 → None（不下單）；symbol 取 decision.tw_stock_id。
    """
    if decision.abstained:
        logger.info("[Broker] %s abstain，不下單", decision.tw_stock_id)
        return None
    side = _ACTION_SIDE.get(decision.action)
    if side is None:  # HOLD
        logger.info("[Broker] %s 判定 Hold，不下單", decision.tw_stock_id)
        return None
    qty = base_quantity * _ACTION_SIZE_FRACTION[decision.action]
    if qty <= 0:
        return None
    return broker.place_order(decision.tw_stock_id, side, qty)
