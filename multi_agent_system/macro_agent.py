"""macro_agent.py — 總經專家 (Macroeconomic Agent)。

單一職責
--------
評估「系統性風險」，輸出大盤健康度 (macro health) ∈ [0, 1]（1=最健康）。

金融原理與計算式
----------------
健康度為三個子分量的加權平均（子權重見 config.MACRO_SUBWEIGHTS）：

1) 殖利率曲線 (Yield Curve, 10Y-2Y)
   * 原理：長短天期公債利差是最可靠的衰退領先指標之一。曲線倒掛
     (spread <= 0) 往往領先景氣衰退 6~18 個月 (Estrella & Mishkin, 1996)。
   * 公式：
         spread_pct = yield_10Y(%) - yield_2Y(%)
         curve_score = clamp( (spread_pct - 0) / (HEALTHY - 0), 0, 1 )
     即 spread <= 0 → 0 分（最大壓力）；spread >= HEALTHY(1.5%) → 1 分。

2) 通膨 (CPI YoY)
   * 原理：通膨過高 → 央行升息 → 資金成本上揚、估值收縮（risk-off）。
   * 公式（越低越健康）：
         cpi_score = clamp( (HOT - cpi) / (HOT - TARGET), 0, 1 )
     即 cpi <= TARGET(2%) → 1 分；cpi >= HOT(5%) → 0 分。

3) 市場情緒 (News Sentiment, 選配)
   * 原理：新聞情緒是即時的系統性 risk-on/off 溫度計，補足硬數據的落後性。
   * 公式（sentiment ∈ [-1,1] → [0,1]）：
         sentiment_score = (s + 1) / 2
   * 缺席時（無相關新聞）不臆造中性值，而是把 curve/cpi 子權重「重新歸一化」
     並在輸出帶旗標 sentiment_available=False。

    macro_health = Σ_i w_i * subscore_i        （w_i 為（可能重新歸一化後的）子權重）
"""

from __future__ import annotations

from config import (
    CPI_HOT_PCT,
    CPI_TARGET_PCT,
    MACRO_SUBWEIGHTS,
    YIELD_HEALTHY_PCT,
    YIELD_INVERSION_PCT,
)

from .contracts import AgentVerdict, MacroReading
from .numerics import clamp, linear_map

AGENT_NAME = "MacroAgent"


class MacroeconomicAgent:
    """總經/系統性風險專家。"""

    def evaluate(
        self,
        reading: MacroReading,
        news_sentiment_mean: float | None = None,
    ) -> AgentVerdict:
        if reading is None:
            return AgentVerdict.unavailable(AGENT_NAME, "無總經資料輸入")

        # --- 子分量 1：殖利率曲線 ---
        curve_score = linear_map(
            reading.yield_spread_pct,
            YIELD_INVERSION_PCT,
            YIELD_HEALTHY_PCT,
            0.0,
            1.0,
        )
        inverted = reading.yield_spread_pct <= YIELD_INVERSION_PCT

        # --- 子分量 2：CPI ---
        cpi_score = linear_map(
            reading.cpi_yoy_pct,
            CPI_TARGET_PCT,
            CPI_HOT_PCT,
            1.0,
            0.0,
        )
        cpi_hot = reading.cpi_yoy_pct >= CPI_HOT_PCT

        # --- 子分量 3：新聞情緒（選配）---
        w_curve = MACRO_SUBWEIGHTS["curve"]
        w_cpi = MACRO_SUBWEIGHTS["cpi"]
        w_sent = MACRO_SUBWEIGHTS["sentiment"]

        sentiment_available = news_sentiment_mean is not None
        if sentiment_available:
            sentiment_score = linear_map(float(news_sentiment_mean), -1.0, 1.0, 0.0, 1.0)
            health = w_curve * curve_score + w_cpi * cpi_score + w_sent * sentiment_score
        else:
            # 重新歸一化 curve/cpi（不臆造中性情緒）。
            renorm = w_curve + w_cpi
            sentiment_score = None
            health = (w_curve * curve_score + w_cpi * cpi_score) / renorm

        health = clamp(health, 0.0, 1.0)

        reason = self._build_reason(
            reading, inverted, cpi_hot, sentiment_available
        )
        diagnostics = {
            "yield_spread_pct": reading.yield_spread_pct,
            "cpi_yoy_pct": reading.cpi_yoy_pct,
            "curve_score": round(curve_score, 4),
            "cpi_score": round(cpi_score, 4),
            "sentiment_score": (
                round(sentiment_score, 4) if sentiment_score is not None else None
            ),
            "inverted": inverted,
            "cpi_hot": cpi_hot,
            "sentiment_available": sentiment_available,
            "is_simulated": reading.is_simulated,
            "source": reading.source,
            "as_of": reading.as_of,
        }
        return AgentVerdict(
            agent=AGENT_NAME,
            available=True,
            score=round(health, 4),
            reason=reason,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _build_reason(
        reading: MacroReading,
        inverted: bool,
        cpi_hot: bool,
        sentiment_available: bool,
    ) -> str:
        parts: list[str] = []
        if inverted:
            parts.append(
                f"殖利率曲線倒掛 (10Y-2Y={reading.yield_spread_pct:+.2f}%)，衰退風險升高"
            )
        else:
            parts.append(f"殖利率利差 {reading.yield_spread_pct:+.2f}% 未倒掛")
        if cpi_hot:
            parts.append(f"通膨過熱 (CPI YoY={reading.cpi_yoy_pct:.2f}%)，升息壓力大")
        else:
            parts.append(f"CPI YoY={reading.cpi_yoy_pct:.2f}%")
        if not sentiment_available:
            parts.append("無新聞情緒資料，子權重已重新歸一化")
        if reading.is_simulated:
            parts.append("⚠️ 本次總經為模擬情境（非實測數據）")
        return "；".join(parts)
