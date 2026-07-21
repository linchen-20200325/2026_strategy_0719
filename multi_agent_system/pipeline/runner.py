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

from config import SESSION_LABELS

from ..contracts import Action
from ..integration_agent import CycleResult, ResearchRequest, WorkflowOrchestrator
from ..macro_providers import MacroDataProvider
from ..notifications import Notifier, format_notification, should_notify
from .freshness import FreshnessReport, check_freshness
from .watchlist import WatchItem

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


def format_run_digest(report: RunReport) -> str:
    """一則彙整訊息（供 LINE 推播:一輪一則,不逐訊號洗版）。"""
    day = report.ran_at[:10]
    label = SESSION_LABELS.get(report.session, report.session)
    head = f"📊 多智能體投研｜{label} {day}"
    fresh = (
        "✅ 資料新鮮"
        if report.freshness.all_fresh
        else f"⚠️ 資料過期：{report.freshness.stale_names}"
    )
    lines = [head, fresh]
    actionable = report.actionable()
    if actionable:
        lines.extend(format_notification(r.decision) for r in actionable)
    else:
        lines.append("（本輪無可行動訊號）")
    holds = len(report.results) - len(actionable)
    if holds:
        lines.append(f"其餘 {holds} 檔:觀望 / 資料不足")
    return "\n".join(lines)


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


def format_bullish_digest(results: Sequence[CycleResult], *, title: str = "📈 目前利多標的") -> str:
    """利多榜 → 一則 LINE 文字（供推播）。無利多時誠實說明。"""
    ranked = bullish_ranked(results)
    if not ranked:
        return f"{title}\n（目前無利多訊號）"
    lines = [title]
    lines.extend(f"{i}. {format_notification(r.decision)}" for i, r in enumerate(ranked, 1))
    return "\n".join(lines)


# ── 個股盯盤卡（對齊使用者現有 LINE 盯盤 bot：技術＋籌碼一張卡）─────────────────
# 五大行動 → 三態白話標籤（利多/中性/利空）;emoji + 中文並用（不靠顏色單獨表意）。
_VERDICT_LABEL: dict[Action, str] = {
    Action.STRONG_BUY: "🟢 利多",
    Action.ADD: "🟢 利多",
    Action.HOLD: "🟡 中性",
    Action.REDUCE: "🔴 利空",
    Action.STRONG_SELL: "🔴 利空",
}


def _fmt_price(v: float) -> str:
    """價格顯示：四捨五入 2 位並去尾零（960.0→960、68.90→68.9）。"""
    return f"{round(float(v), 2):g}"


def _ma_seg(label: str, close: float, ma: float | None) -> str:
    """均線段：站上/跌破 + 乖離%（ma 缺 / 0 → 顯示「—」,不捏造）。"""
    if ma is None or ma == 0:
        return f"{label} —"
    pct = (close / ma - 1.0) * 100.0
    status = "✅站上" if close >= ma else "❌跌破"
    return f"{label}{status}({pct:+.1f}%)"


def _lots(v: float | None) -> str:
    """籌碼張數：帶正負號 + 千分位（None → 「—」;賣超保留負號）。"""
    return "—" if v is None else f"{v:+,.0f}張"


def _tech_line(t) -> str:
    parts = [
        f"收{_fmt_price(t.close)}",
        _ma_seg("20MA", t.close, t.ma20),
        _ma_seg("60MA", t.close, t.ma60),
        (f"KD {t.kd_k:g}/{t.kd_d:g}" if t.kd_k is not None and t.kd_d is not None else "KD —"),
        f"RSI {t.rsi:.0f}",
    ]
    return " | ".join(parts)


def _chip_line(t) -> str:
    if t.foreign_net_lots is None and t.trust_net_lots is None and t.total_net_lots is None:
        return ""
    return (
        f"外資{_lots(t.foreign_net_lots)} | 投信{_lots(t.trust_net_lots)} | "
        f"三大法人{_lots(t.total_net_lots)}"
    )


def _fin_line(f) -> str:
    """最新季報：EPS / 營收（千元→億）/ 毛利率 / 淨利率（缺欄略過，全缺回空字串）。"""
    if f is None:
        return ""
    parts: list[str] = []
    if f.eps is not None:
        parts.append(f"EPS {f.eps:g}")
    if f.revenue_k is not None:
        parts.append(f"營收 {f.revenue_k / 1e5:.1f}億")   # 千元 → 億（÷1e5）
    if f.gross_margin_pct is not None:
        parts.append(f"毛利率 {f.gross_margin_pct:.1f}%")
    if f.net_margin_pct is not None:
        parts.append(f"淨利率 {f.net_margin_pct:.1f}%")
    if not parts:
        return ""
    return f"📈 {f.period_label}季報 " + " · ".join(parts)


def _news_line(packet, news_summary: str | None) -> str:
    """新聞：優先 AI 總結；無 AI（無 key/失敗）→ 退回頭條標題；皆無 → 空字串。"""
    if news_summary:
        return "📰 " + news_summary
    if packet.news:
        tops = "；".join(n.title for n in packet.news[:2])
        return "📰 " + tops
    return ""


def format_stock_card(result: CycleResult, *, news_summary: str | None = None) -> str:
    """單一標的盯盤卡：判讀 → 📊 技術 → 💰 籌碼 → 📰 新聞（AI 總結/頭條）→ 📈 最新季報。

    資料缺席一律誠實呈現（判讀 abstain → 「資料不足」;技術缺 → 「—」;籌碼/新聞/財報缺 → 略過該行）。
    news_summary 由推播端（multiuser）先以 Gemini 產好傳入；未傳則退回頭條標題（不杜撰）。
    """
    d = result.decision
    if d.abstained or d.final_score is None:
        head = f"【{d.tw_stock_id}】⬜ 資料不足"
    else:
        head = f"【{d.tw_stock_id}】{_VERDICT_LABEL[d.action]}　Final={d.final_score:.2f}"
    lines = [head]

    tech = result.packet.technical
    if tech is None:
        lines.append("📊 技術 —（stock.db 查無或缺值）")
    else:
        lines.append("📊 技術 " + _tech_line(tech))
        chip = _chip_line(tech)
        if chip:
            lines.append("💰 籌碼 " + chip)

    news = _news_line(result.packet, news_summary)
    if news:
        lines.append(news)
    fin = _fin_line(result.packet.financials)
    if fin:
        lines.append(fin)
    return "\n".join(lines)


def format_watch_digest(
    results: Sequence[CycleResult],
    *,
    day: str,
    title: str = "📈 個股盯盤",
    news_summaries: dict[str, str] | None = None,
) -> str:
    """全清單盯盤（對齊 LINE 盯盤 bot）：每檔一張卡（判讀＋技術＋籌碼＋新聞＋財報），含指令頁尾。

    與 `format_bullish_digest`（只推利多榜）不同：本函式**逐檔全列**，即使中性/利空也列出，
    符合「每天固定收到自己清單狀態」的盯盤體驗。
    news_summaries：{stock_id: AI 新聞總結}，由推播端先產好；某檔缺 → 該卡退回頭條標題。
    """
    head = f"{title} {day}"
    if not results:
        return f"{head}\n（清單為空）"
    summaries = news_summaries or {}
    cards = [
        format_stock_card(r, news_summary=summaries.get(r.decision.tw_stock_id))
        for r in results
    ]
    footer = "（僅供參考，非投資建議。指令：加/刪/清單）"
    return "\n\n".join([head, *cards, footer])


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
