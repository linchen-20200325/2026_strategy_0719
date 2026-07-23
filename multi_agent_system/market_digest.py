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
    CPI_TARGET_PCT,
    DIGEST_NEWS_TOP_N,
    DIGEST_SENTIMENT_BEARISH_MAX,
    DIGEST_SENTIMENT_BULLISH_MIN,
    MARKET_REGIME_BEAR_MAX,
    MARKET_REGIME_BULL_MIN,
    NIGHT_BIG_MOVE_PCT,
    NIGHT_SMALL_MOVE_PCT,
    PMI_EXPANSION_LEVEL,
    PMI_REGIME_SPAN,
    REGIME_LABEL_BEAR,
    REGIME_LABEL_BULL,
    REGIME_LABEL_NEUTRAL,
    SENTIMENT_RAW_MAX,
    SENTIMENT_RAW_MIN,
    YIELD_HEALTHY_PCT,
    YIELD_INVERSION_PCT,
)

from .contracts import CycleResult, MacroReading, NewsItem, TwMacroReading, TwNightReading
from .numerics import linear_map


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
        tone = d.action.tone
        if tone == "bullish" and not d.abstained:
            bullish += 1
            names.append(d.tw_stock_id)
        elif tone == "bearish":
            bearish += 1
        else:
            hold += 1
    return WatchTally(len(results), bullish, hold, bearish, names)


def night_regime(chg_pct: float) -> str:
    """台指夜盤漲跌 % → 五分類 + 隔日開盤傾向（門檻走 config SSOT）。"""
    a = abs(chg_pct)
    if a < NIGHT_SMALL_MOVE_PCT:
        return "持平，隔日開平"
    big = a >= NIGHT_BIG_MOVE_PCT
    if chg_pct > 0:
        return "大漲，隔日偏多開高" if big else "小漲，隔日偏多"
    return "大跌，隔日偏空開低" if big else "小跌，隔日偏空"


def _regime_word(score: float) -> str:
    """綜合偏多度 [0,1] → 偏多 / 中性 / 偏空（門檻 + 標籤字串皆走 config SSOT）。"""
    if score >= MARKET_REGIME_BULL_MIN:
        return REGIME_LABEL_BULL
    if score <= MARKET_REGIME_BEAR_MAX:
        return REGIME_LABEL_BEAR
    return REGIME_LABEL_NEUTRAL


def _sentiment_bull(mean: float) -> float:
    """新聞情緒 mean → 偏多度 [0,1]（範圍走 config SENTIMENT_RAW_MIN/MAX SSOT；+1→1 / 0→0.5 / -1→0）。"""
    return linear_map(mean, SENTIMENT_RAW_MIN, SENTIMENT_RAW_MAX, 0.0, 1.0)


def market_regime(
    macro: MacroReading,
    tw_macro: TwMacroReading | None,
    night: TwNightReading | None,
    intl_news: NewsStat,
    tw_news: NewsStat,
) -> tuple[str, float, list[str]]:
    """規則式「大盤判讀」：綜合 5 面向 → 偏多/中性/偏空 + 綜合偏多度 + 各面向解讀。

    面向（缺料者不計入、不臆造，§1 Fail-Loud）：
      1) 美股/全球總經：殖利率曲線 + CPI（高 CPI 壓分）
      2) 台股總經：PMI 榮枯 + 外資買賣超方向
      3) 台指夜盤：夜盤漲跌 → 隔日開盤傾向
      4) 美股新聞情緒 / 5) 台股新聞情緒：mean ∈[-1,1] → 偏多度
    每面向映射偏多度 ∈[0,1]，等權平均。數字/門檻全走 config SSOT，可重現、無 LLM。
    """
    dims: list[float] = []
    reasons: list[str] = []

    curve = linear_map(macro.yield_spread_pct, YIELD_INVERSION_PCT, YIELD_HEALTHY_PCT, 0.0, 1.0)
    cpi = linear_map(macro.cpi_yoy_pct, CPI_HOT_PCT, CPI_TARGET_PCT, 0.0, 1.0)
    macro_score = (curve + cpi) / 2.0
    dims.append(macro_score)
    reasons.append(
        f"美股總經{_regime_word(macro_score)}"
        f"（利差{macro.yield_spread_pct:+.2f}%·CPI{macro.cpi_yoy_pct:.1f}%）"
    )

    if tw_macro is not None:
        tw_parts: list[float] = []
        tw_desc: list[str] = []
        if tw_macro.pmi is not None:
            tw_parts.append(
                linear_map(
                    tw_macro.pmi,
                    PMI_EXPANSION_LEVEL - PMI_REGIME_SPAN,
                    PMI_EXPANSION_LEVEL + PMI_REGIME_SPAN,
                    0.0, 1.0,
                )
            )
            tw_desc.append("PMI擴張" if tw_macro.pmi >= PMI_EXPANSION_LEVEL else "PMI收縮")
        if tw_macro.foreign_net_yi is not None:
            tw_parts.append(1.0 if tw_macro.foreign_net_yi > 0 else 0.0)
            tw_desc.append("外資買超" if tw_macro.foreign_net_yi > 0 else "外資賣超")
        if tw_parts:
            tw_score = sum(tw_parts) / len(tw_parts)
            dims.append(tw_score)
            reasons.append(f"台股總經{_regime_word(tw_score)}（{'·'.join(tw_desc)}）")

    if night is not None and night.night_chg_pct is not None:
        # 夜盤漲跌 → 偏多度：-NIGHT_BIG→0 / 0→0.5 / +NIGHT_BIG→1（範圍走 config SSOT）。
        night_score = linear_map(
            night.night_chg_pct, -NIGHT_BIG_MOVE_PCT, NIGHT_BIG_MOVE_PCT, 0.0, 1.0
        )
        dims.append(night_score)
        reasons.append(f"夜盤{_regime_word(night_score)}（{night.night_chg_pct:+.1f}%）")

    if intl_news.mean is not None:
        dims.append(_sentiment_bull(intl_news.mean))
        reasons.append(f"美股情緒{sentiment_label(intl_news.mean)}（{intl_news.mean:+.2f}）")

    if tw_news.mean is not None:
        dims.append(_sentiment_bull(tw_news.mean))
        reasons.append(f"台股情緒{sentiment_label(tw_news.mean)}（{tw_news.mean:+.2f}）")

    overall = sum(dims) / len(dims)
    return _regime_word(overall), overall, reasons
