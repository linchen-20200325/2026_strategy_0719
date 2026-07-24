"""run_digest.py — 排程一輪結果 / 個股盯盤卡的文字渲染（LINE / console 共用）。

把 pipeline 的 L2 結果（RunReport / CycleResult）攤成推播文字。純顯示層、無 I/O、無判斷：
* 資料由 caller 傳入（duck-typing 讀屬性）。
* 篩選 / 排序（bullish_ranked）仍住 `pipeline.runner`（L2）;`format_bullish_digest` 需要時以
  **函式內 import** 取用，避免載入期循環相依（render_text 不得於載入期 import runner）。

盯盤卡對齊使用者現有 LINE 盯盤 bot（技術＋籌碼一張卡）;資料缺席一律誠實呈現，不杜撰、不經 LLM。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from config import SESSION_LABELS

from ..notifications import emoji_for, format_notification
from ._common import _fmt_price

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..contracts import CycleResult
    from ..pipeline.runner import RunReport


# ── 個股盯盤卡（對齊使用者現有 LINE 盯盤 bot：技術＋籌碼一張卡）─────────────────
# 三態情緒 → 白話標籤（利多/中性/利空）;emoji + 中文並用（不靠顏色單獨表意）。
# 5→3 分類走 Action.tone SSOT（見 contracts），此處不重刻五大行動歸類，避免漂移。
_TONE_LABEL: dict[str, str] = {
    "bullish": "🟢 利多",
    "neutral": "🟡 中性",
    "bearish": "🔴 利空",
}


# agent key → 中文短標（判讀理由露出用）。
_AGENT_ZH: dict[str, str] = {
    "macro": "總經", "technical": "技術", "fundamental": "基本", "allocation": "配置",
}


def _verdict_line(decision) -> str:
    """判讀理由：4 專家評分（0~1）攤開，讓 Final 可追溯（規則式，非 LLM）。缺專家略過。"""
    verdicts = decision.verdicts or {}
    parts: list[str] = []
    for key in ("macro", "technical", "fundamental", "allocation"):
        v = verdicts.get(key)
        if v is not None and v.score is not None:
            parts.append(f"{_AGENT_ZH[key]}{v.score:.2f}")
    return "🧮 判讀 " + " · ".join(parts) if parts else ""


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


def _news_line(packet) -> str:
    """新聞：顯示 news.db 真實頭條標題（最多 2 則）；無新聞 → 空字串。"""
    if packet.news:
        tops = "；".join(n.title for n in packet.news[:2])
        return "📰 " + tops
    return ""


def format_stock_card(result: CycleResult) -> str:
    """單一標的盯盤卡：判讀 → 📊 技術 → 💰 籌碼 → 📰 新聞頭條 → 📈 最新季報。

    資料缺席一律誠實呈現（判讀 abstain → 「資料不足」;技術缺 → 「—」;籌碼/新聞/財報缺 → 略過該行）。
    新聞只顯示 news.db 真實頭條標題（不經 LLM、不杜撰）。
    """
    d = result.decision
    if d.abstained or d.final_score is None:
        head = f"【{d.tw_stock_id}】⬜ 資料不足"
    else:
        head = f"【{d.tw_stock_id}】{_TONE_LABEL[d.action.tone]}　Final={d.final_score:.2f}"
    lines = [head]
    if not (d.abstained or d.final_score is None):
        vline = _verdict_line(d)
        if vline:
            lines.append(vline)

    tech = result.packet.technical
    if tech is None:
        lines.append("📊 技術 —（stock.db 查無或缺值）")
    else:
        lines.append("📊 技術 " + _tech_line(tech))
        chip = _chip_line(tech)
        if chip:
            lines.append("💰 籌碼 " + chip)

    news = _news_line(result.packet)
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
) -> str:
    """全清單盯盤（對齊 LINE 盯盤 bot）：每檔一張卡（判讀＋技術＋籌碼＋新聞＋財報），含指令頁尾。

    與 `format_bullish_digest`（只推利多榜）不同：本函式**逐檔全列**，即使中性/利空也列出，
    符合「每天固定收到自己清單狀態」的盯盤體驗。新聞只顯示真實頭條（不經 LLM）。
    """
    head = f"{title} {day}"
    if not results:
        return f"{head}\n（清單為空）"
    cards = [format_stock_card(r) for r in results]
    footer = "（僅供參考，非投資建議。指令：加/刪/清單）"
    return "\n\n".join([head, *cards, footer])


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


def format_bullish_digest(results: Sequence[CycleResult], *, title: str = "📈 目前利多標的") -> str:
    """利多榜 → 一則 LINE 文字（供推播）。無利多時誠實說明。"""
    from ..pipeline.runner import bullish_ranked

    ranked = bullish_ranked(results)
    if not ranked:
        return f"{title}\n（目前無利多訊號）"
    lines = [title]
    lines.extend(f"{i}. {format_notification(r.decision)}" for i, r in enumerate(ranked, 1))
    return "\n".join(lines)


def summarize(report: RunReport) -> str:
    """人可讀的一段摘要（供 log / stdout）。"""
    lines = [
        f"===== {report.session} @ {report.ran_at} =====",
        f"新鮮度:{'✅ 全新鮮' if report.freshness.all_fresh else '⚠️ ' + str(report.freshness.stale_names)}",
    ]
    for r in report.results:
        d = r.decision
        emoji = emoji_for(d.action)
        score = "N/A" if d.final_score is None else f"{d.final_score:.3f}"
        lines.append(f"  {emoji} {d.tw_stock_id}　{d.action.value}　Final={score}")
    return "\n".join(lines)
