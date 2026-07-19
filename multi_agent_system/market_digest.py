"""market_digest.py — 「國際情勢 + 台股」市場快訊（broadcast，mynews 風格）。

與 per-user 個股利多分工（同 mynews：主報告 broadcast + 個股盯盤 per-user）：
本檔組**市場級**一則、發全體好友；個股利多走 multiuser.run_per_user_push 逐人。

資料來源（皆已在系統內，無新外部相依、無 AI）：
* 國際情勢：MacroReading（殖利率 10Y-2Y 倒掛 / CPI 過熱）+ news.db 外電情緒統計。
* 台股：追蹤清單經 6-agent 的訊號統計（利多/觀望/偏空）+ news.db 台股情緒。

Fail-Loud：macro 為模擬值 → 明標「(模擬)」;某區塊無新聞 → 誠實寫「無資料」,不臆造中性。
純函式（無 I/O）：news 由 DataAggregationAgent.fetch_news 抓好後傳入,便於單測。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from config import (
    CPI_HOT_PCT,
    DIGEST_NEWS_TOP_N,
    DIGEST_SENTIMENT_BEARISH_MAX,
    DIGEST_SENTIMENT_BULLISH_MIN,
    YIELD_INVERSION_PCT,
)

from .contracts import Action, MacroReading, NewsItem
from .integration_agent import CycleResult

_SESSION_LABEL = {"morning": "早盤前", "afternoon": "收盤後"}


@dataclass(frozen=True)
class NewsStat:
    count: int
    mean: float | None            # 平均 sentiment ∈ [-1,1];無新聞 → None
    top_titles: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WatchTally:
    n: int
    bullish: int
    hold: int
    bearish: int
    bullish_names: list[str] = field(default_factory=list)


def sentiment_label(mean: float | None) -> str:
    """近期平均情緒 → 偏多 / 中性 / 偏空 / 無資料（門檻走 config SSOT）。"""
    if mean is None:
        return "無資料"
    if mean >= DIGEST_SENTIMENT_BULLISH_MIN:
        return "偏多"
    if mean <= DIGEST_SENTIMENT_BEARISH_MAX:
        return "偏空"
    return "中性"


def summarize_news(items: Sequence[NewsItem], *, top_n: int = DIGEST_NEWS_TOP_N) -> NewsStat:
    """一組新聞 → (則數, 平均情緒, 最強頭條)。空 → count=0, mean=None（Fail-Loud）。"""
    if not items:
        return NewsStat(0, None, [])
    mean = sum(n.sentiment_score for n in items) / len(items)
    top = sorted(items, key=lambda n: abs(n.sentiment_score), reverse=True)[:top_n]
    return NewsStat(len(items), mean, [n.title for n in top])


def tally_watchlist(results: Sequence[CycleResult]) -> WatchTally:
    """追蹤清單決策 → 利多 / 觀望 / 偏空 檔數（利多 = 強買/加碼且非棄權）。"""
    bullish = hold = bearish = 0
    names: list[str] = []
    for r in results:
        d = r.decision
        if d.action.is_bullish and not d.abstained:
            bullish += 1
            names.append(d.tw_stock_id)
        elif d.action in (Action.REDUCE, Action.STRONG_SELL):
            bearish += 1
        else:
            hold += 1
    return WatchTally(len(results), bullish, hold, bearish, names)


def _macro_line(macro: MacroReading) -> str:
    inverted = macro.yield_spread_pct <= YIELD_INVERSION_PCT
    hot = macro.cpi_yoy_pct >= CPI_HOT_PCT
    curve = "⚠️倒掛" if inverted else "正常"
    cpi = "🔥偏熱" if hot else "溫和"
    sim = "（模擬）" if macro.is_simulated else ""
    return (f"🌍 殖利率 10Y-2Y {macro.yield_spread_pct:+.2f}%（{curve}）· "
            f"CPI {macro.cpi_yoy_pct:.1f}%（{cpi}）{sim}")


def _news_block(icon_label: str, stat: NewsStat) -> list[str]:
    if stat.count == 0 or stat.mean is None:
        return [f"{icon_label}：無資料（近日無相關新聞）"]
    lines = [f"{icon_label}：{sentiment_label(stat.mean)}（近 {stat.count} 則，均 {stat.mean:+.2f}）"]
    lines += [f"　・{t}" for t in stat.top_titles]
    return lines


def build_market_digest(
    *,
    session: str,
    day: str,
    macro: MacroReading,
    intl_news: NewsStat,
    tw_news: NewsStat,
    tally: WatchTally,
) -> str:
    """組一則市場快訊（mynews 風格 emoji 分區）。day 為 'MM/DD' 或 ISO 前綴。"""
    label = _SESSION_LABEL.get(session, session)
    lines = [
        f"🌐 市場快訊｜{label} {day}",
        "━━ 國際情勢 ━━",
        _macro_line(macro),
        *_news_block("📰 外電情緒", intl_news),
        "━━ 台股 ━━",
        f"🇹🇼 追蹤 {tally.n} 檔 → 🟢利多 {tally.bullish} / 🟡觀望 {tally.hold} / 🔴偏空 {tally.bearish}",
    ]
    if tally.bullish_names:
        lines.append(f"📈 利多：{'、'.join(tally.bullish_names)}")
    lines += _news_block("📰 台股新聞情緒", tw_news)
    return "\n".join(lines)
