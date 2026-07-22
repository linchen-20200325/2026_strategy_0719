"""ledger — 判讀 forward-test 對帳（把每次大盤判讀存檔，T+N 交易日後用實際報酬評分）。

模組:
* reconcile — 純函式對帳核心（進出場對齊 + 前瞻報酬 + 命中判定），無 I/O。
* store     — append-only JSONL 持久化（只存不可變判讀事實）。L1。
* recorder  — 把大盤判讀寫進 ledger（record 階段;失敗不擋推播）。L2。
* report    — stateless 聚合命中率（每次用當前 market_index 重算）。L2 純函式。
"""

from __future__ import annotations

from .reconcile import (
    PriceBar,
    ReconcileOutcome,
    classify_hit,
    forward_return,
    reconcile,
)
from .recorder import record_market_regime, regime_of
from .report import (
    BucketStat,
    EquityReport,
    LedgerReport,
    build_equity,
    build_report,
    dedup_judgments,
    format_equity,
    format_report,
)
from .stock_recorder import record_stock_judgments
from .stock_store import StockJudgment, append_stock_judgments, read_stock_judgments
from .store import Judgment, append_judgment, read_judgments

__all__ = [
    "PriceBar",
    "ReconcileOutcome",
    "classify_hit",
    "forward_return",
    "reconcile",
    "Judgment",
    "append_judgment",
    "read_judgments",
    "record_market_regime",
    "regime_of",
    "BucketStat",
    "EquityReport",
    "LedgerReport",
    "build_equity",
    "build_report",
    "dedup_judgments",
    "format_equity",
    "format_report",
    # A Phase 1 — 個股判讀 forward-test（止血落帳；對帳於 Phase 2）
    "StockJudgment",
    "append_stock_judgments",
    "read_stock_judgments",
    "record_stock_judgments",
]
