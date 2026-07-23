"""market.py — 「國際情勢 + 台股」市場快訊文字渲染（broadcast，mynews 風格）。

把 market_digest 的 L2 判讀 / 統計結果攤成一則 LINE / console 文字。純顯示層、無 I/O：
* 資料（MacroReading / TwMacroReading / TwNightReading / NewsStat / WatchTally）由 caller 傳入。
* 判讀 / 統計（market_regime / night_regime / sentiment_label）仍住 `market_digest`（L2）;
  本層需要時以 **函式內 import** 取用，避免載入期循環相依（render_text 不得於載入期 import
  market_digest）。

Fail-Loud：macro 為模擬值 → 明標「（模擬）」;某區塊無新聞 → 誠實寫「無資料」,不臆造中性。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from config import (
    CPI_HOT_PCT,
    PMI_EXPANSION_LEVEL,
    SESSION_LABELS,
    YIELD_INVERSION_PCT,
)

if TYPE_CHECKING:
    from ..contracts import MacroReading, TwMacroReading, TwNightReading
    from ..market_digest import NewsStat, WatchTally


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


def _night_lines(night: TwNightReading) -> list[str]:
    """盤前訊號 0~2 行：台指期外資留倉（口）+ 台指夜盤漲跌→隔日開盤傾向。缺者不列。"""
    from ..market_digest import night_regime

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
    from ..market_digest import sentiment_label

    if stat.count == 0 or stat.mean is None:
        return [f"{icon_label}：無資料（近日無相關新聞）"]
    lines = [f"{icon_label}：{sentiment_label(stat.mean)}（近 {stat.count} 則，均 {stat.mean:+.2f}）"]
    lines += [f"　・{t}" for t in stat.top_titles]
    return lines


def _regime_lines(
    macro: MacroReading,
    tw_macro: TwMacroReading | None,
    night: TwNightReading | None,
    intl_news: NewsStat,
    tw_news: NewsStat,
) -> list[str]:
    """🧭 大盤判讀段（規則式綜合解讀；放快訊末做「解讀結果」收尾）。"""
    from ..market_digest import market_regime

    label, overall, reasons = market_regime(macro, tw_macro, night, intl_news, tw_news)
    return [
        "━━ 🧭 大盤判讀（規則式綜合）━━",
        f"{label}（綜合偏多度 {overall:.2f}）",
        "　└ " + " · ".join(reasons),
    ]


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
    lines += _regime_lines(macro, tw_macro, night, intl_news, tw_news)
    return "\n".join(lines)
