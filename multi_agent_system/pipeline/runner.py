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
from datetime import UTC, date, datetime

from ..contracts import Action
from ..integration_agent import CycleResult, ResearchRequest, WorkflowOrchestrator
from ..macro_providers import MacroDataProvider
from ..notifications import Notifier, should_notify
from .freshness import FreshnessReport, check_freshness
from .watchlist import WatchItem

logger = logging.getLogger("multi_agent_system.pipeline")

VALID_SESSIONS = ("morning", "afternoon")


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
            ResearchRequest(
                tw_stock_id=it.tw_stock_id,
                us_stock_id=it.us_stock_id,
                news_keywords=list(it.keywords),
                portfolio_state=it.portfolio_state(),
                macro_provider=self.macro_provider,
                as_of_date=as_of,
                auto_trade=auto_trade,
            )
            for it in self.watchlist
        ]
        results = self.orchestrator.run_batch(requests)

        # 3) 通知（只推可行動訊號）
        if self.notifier is not None:
            for r in results:
                if should_notify(r.decision):
                    self.notifier.notify(r.decision)

        ran_at = datetime.now(UTC).isoformat()
        report = RunReport(session=session, ran_at=ran_at, freshness=fresh, results=results)
        n_act = len(report.actionable())
        logger.info(
            "[%s] 完成:%d 標的,%d 個可行動訊號", session, len(results), n_act
        )
        return report


def summarize(report: RunReport) -> str:
    """人可讀的一段摘要（供 log / stdout）。"""
    lines = [
        f"===== {report.session} @ {report.ran_at} =====",
        f"新鮮度:{'✅ 全新鮮' if report.freshness.all_fresh else '⚠️ ' + str(report.freshness.stale_names)}",
    ]
    for r in report.results:
        d = r.decision
        emoji = {
            Action.STRONG_BUY: "🟢", Action.ADD: "🟢", Action.HOLD: "🟡",
            Action.REDUCE: "🟠", Action.STRONG_SELL: "🔴",
        }[d.action]
        score = "N/A" if d.final_score is None else f"{d.final_score:.3f}"
        lines.append(f"  {emoji} {d.tw_stock_id}　{d.action.value}　Final={score}")
    return "\n".join(lines)
