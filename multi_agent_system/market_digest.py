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
    NIGHT_BIG_MOVE_PCT,
    NIGHT_SMALL_MOVE_PCT,
    PMI_EXPANSION_LEVEL,
    SESSION_LABELS,
    YIELD_INVERSION_PCT,
)

from .contracts import Action, MacroReading, NewsItem, TwMacroReading, TwNightReading
from .integration_agent import CycleResult


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


def _tw_macro_line(tw: TwMacroReading) -> str:
    """台股總經一行：PMI 榮枯 + 外資買賣超（億元）。單一指標缺 → 誠實寫「資料不足」。"""
    parts: list[str] = []
    if tw.pmi is not None:
        regime = "擴張" if tw.pmi >= PMI_EXPANSION_LEVEL else "收縮"
        parts.append(f"PMI {tw.pmi:.1f}（{regime}）")
    else:
        parts.append("PMI 資料不足")
    if tw.foreign_net_yi is not None:
        flow = "買超" if tw.foreign_net_yi >= 0 else "賣超"
        parts.append(f"外資 {tw.foreign_net_yi:+.0f} 億（{flow}）")
    else:
        parts.append("外資 資料不足")
    sim = "（模擬）" if tw.is_simulated else ""
    return f"📊 {' · '.join(parts)}{sim}"


def night_regime(chg_pct: float) -> str:
    """台指夜盤漲跌 % → 五分類 + 隔日開盤傾向（門檻走 config SSOT）。"""
    a = abs(chg_pct)
    if a < NIGHT_SMALL_MOVE_PCT:
        return "持平，隔日開平"
    big = a >= NIGHT_BIG_MOVE_PCT
    if chg_pct > 0:
        return "大漲，隔日偏多開高" if big else "小漲，隔日偏多"
    return "大跌，隔日偏空開低" if big else "小跌，隔日偏空"


def _night_lines(night: TwNightReading) -> list[str]:
    """盤前訊號 0~2 行：台指期外資留倉（口）+ 台指夜盤漲跌→隔日開盤傾向。缺者不列。"""
    lines: list[str] = []
    if night.foreign_fut_oi_lots is not None:
        lots = night.foreign_fut_oi_lots
        bias = "偏多" if lots > 0 else ("偏空" if lots < 0 else "中性")
        lines.append(f"🌙 台指期外資留倉 {lots:+,.0f} 口（{bias}）")
    if night.night_close is not None:
        seg = f"🌙 台指夜盤 {night.night_close:g}"
        if night.night_chg_pct is not None:
            pts = f"{night.night_chg_pts:+.0f} 點 / " if night.night_chg_pts is not None else ""
            seg += f"（{pts}{night.night_chg_pct:+.1f}% → {night_regime(night.night_chg_pct)}）"
        lines.append(seg)
    if lines and night.is_simulated:
        lines[-1] += "（模擬）"
    return lines


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
    tw_macro: TwMacroReading | None = None,
    night: TwNightReading | None = None,
) -> str:
    """組一則市場快訊（mynews 風格 emoji 分區）。day 為 'MM/DD' 或 ISO 前綴。

    * 國際情勢 = 美股/全球總經（macro：10Y-2Y 利差 + CPI）+ 外電情緒。
    * 台股     = 台股總經（tw_macro：PMI + 外資，選填）+ 盤前夜盤訊號（night：台指期
      外資留倉 + 台指夜盤→隔日開盤傾向，選填）+ 追蹤清單訊號統計 + 台股新聞情緒。
      tw_macro / night 省略（None）時不顯示對應行,其餘照舊（向後相容）。
    """
    label = SESSION_LABELS.get(session, session)
    lines = [
        f"🌐 市場快訊｜{label} {day}",
        "━━ 國際情勢（美股 / 全球）━━",
        _macro_line(macro),
        *_news_block("📰 外電情緒", intl_news),
        "━━ 台股 ━━",
    ]
    if tw_macro is not None:
        lines.append(_tw_macro_line(tw_macro))
    if night is not None:
        lines.extend(_night_lines(night))
    lines.append(
        f"🇹🇼 追蹤 {tally.n} 檔 → 🟢利多 {tally.bullish} / 🟡觀望 {tally.hold} / 🔴偏空 {tally.bearish}"
    )
    if tally.bullish_names:
        lines.append(f"📈 利多：{'、'.join(tally.bullish_names)}")
    lines += _news_block("📰 台股新聞情緒", tw_news)
    return "\n".join(lines)
