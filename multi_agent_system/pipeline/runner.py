"""runner.py — 排程批次執行器（供 cron / GitHub Actions 定時呼叫）。

一輪流程：
    1) 新鮮度守門 check_freshness（過期 → 告警；strict 模式 → raise，Fail-Loud）
    2) 對 watchlist 每個標的組 ResearchRequest → orchestrator.run_batch
    3) 對「可行動」訊號逐一 notifier.notify（Console / 未來 LINE）
    4) 回傳可序列化 RunReport（session / 時間 / 新鮮度 / 各標的決策）

本檔無 streamlit 依賴（cron 環境不需 UI）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from ..contracts import CycleResult, WatchItem
from ..integration_agent import ResearchRequest, WorkflowOrchestrator
from ..macro_providers import MacroDataProvider
from ..notifications import Notifier, should_notify
from ..render_text.run_digest import (  # noqa: F401  (向後相容 re-export)
    _fin_line,
    format_bullish_digest,
    format_run_digest,
    format_stock_card,
    format_watch_digest,
    summarize,
)
from .freshness import FreshnessReport, check_freshness

logger = logging.getLogger("multi_agent_system.pipeline")

VALID_SESSIONS = ("morning", "afternoon")


def build_request(
    item: WatchItem,
    macro_provider: MacroDataProvider,
    *,
    as_of: date | None = None,
    auto_trade: bool = False,
) -> ResearchRequest:
    """WatchItem + 總經 provider → ResearchRequest（SSOT，供批次 / 個人化推播共用）。"""
    return ResearchRequest(
        tw_stock_id=item.tw_stock_id,
        us_stock_id=item.us_stock_id,
        news_keywords=list(item.keywords),
        portfolio_state=item.portfolio_state(),
        macro_provider=macro_provider,
        as_of_date=as_of,
        auto_trade=auto_trade,
    )


@dataclass
class RunReport:
    session: str
    ran_at: str                    # ISO UTC
    freshness: FreshnessReport
    results: list[CycleResult]

    def actionable(self) -> list[CycleResult]:
        return [r for r in self.results if should_notify(r.decision)]

    def to_dict(self) -> dict:
        return {
            "session": self.session,
            "ran_at": self.ran_at,
            "freshness": self.freshness.to_dict(),
            "decisions": [
                {
                    "tw_stock_id": r.decision.tw_stock_id,
                    "action": r.decision.action.name,
                    "action_label": r.decision.action.value,
                    "final_score": r.decision.final_score,
                    "abstained": r.decision.abstained,
                    "risk_control_triggered": r.decision.risk_control_triggered,
                    "warnings": list(r.packet.warnings),
                }
                for r in self.results
            ],
        }


class PipelineRunner:
    """把新鮮度守門 + 批次投研 + 通知串成一個可排程的單元。"""

    def __init__(
        self,
        orchestrator: WorkflowOrchestrator,
        watchlist: Sequence[WatchItem],
        macro_provider: MacroDataProvider,
        *,
        db_paths: dict[str, str],
        notifier: Notifier | None = None,
        max_age_days: int = 4,
    ) -> None:
        self.orchestrator = orchestrator
        self.watchlist = list(watchlist)
        self.macro_provider = macro_provider
        self.db_paths = db_paths
        self.notifier = notifier
        self.max_age_days = max_age_days

    def run(
        self,
        session: str,
        *,
        as_of: date | None = None,
        strict_freshness: bool = False,
        auto_trade: bool = False,
    ) -> RunReport:
        if session not in VALID_SESSIONS:
            raise ValueError(f"session 必須為 {VALID_SESSIONS}，收到 {session!r}")

        # 1) 新鮮度守門
        fresh = check_freshness(self.db_paths, as_of=as_of, max_age_days=self.max_age_days)
        if not fresh.all_fresh:
            msg = f"資料過期/缺失：{fresh.stale_names}（as_of={fresh.as_of}）"
            if strict_freshness:
                raise RuntimeError(f"[freshness] {msg} → strict 模式中止,避免用舊資料出訊號")
            logger.warning("[freshness] %s → 續跑,但訊號可信度下降", msg)

        # 2) 批次投研
        requests = [
            build_request(it, self.macro_provider, as_of=as_of, auto_trade=auto_trade)
            for it in self.watchlist
        ]
        results = self.orchestrator.run_batch(requests)

        # 3) 通知（只推可行動訊號）
        if self.notifier is not None:
            for r in results:
                if should_notify(r.decision):
                    self.notifier.notify(r.decision)

        ran_at = datetime.now(timezone.utc).isoformat()
        report = RunReport(session=session, ran_at=ran_at, freshness=fresh, results=results)
        n_act = len(report.actionable())
        logger.info(
            "[%s] 完成:%d 標的,%d 個可行動訊號", session, len(results), n_act
        )
        return report


def bullish_ranked(results: Sequence[CycleResult]) -> list[CycleResult]:
    """篩出「利多」標的（強烈買進 / 適度加碼、非 abstain）並依 Final Score 由高到低排序。"""
    bull = [
        r
        for r in results
        if r.decision.action.is_bullish
        and not r.decision.abstained
        and r.decision.final_score is not None
    ]
    return sorted(bull, key=lambda r: r.decision.final_score, reverse=True)
