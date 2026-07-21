"""technical_agent.py — 技術線型專家 (Technical Analysis Agent)。

單一職責
--------
讀取布林通道 (Bollinger Bands) 與 RSI，判斷價格位階是否進入統計學上的
超買（昂貴）或超賣（便宜）區間，輸出技術面得分 ∈ [0,1]
（方向：越便宜/超賣 → 越高分，利於買進）。

金融原理與計算式
----------------
1) 布林通道 %B（價格在通道中的相對位階）
   * 通道定義（上游 my-stock-dashboard 已算好，k 預設 2）：
         middle = SMA(close, n=20)
         upper  = middle + k * σ(close, n)
         lower  = middle - k * σ(close, n)
   * %B 指標：
         %B = (close - lower) / (upper - lower)
     %B=0 貼下軌（統計上便宜）、%B=1 貼上軌（統計上昂貴）、>1 或 <0 為突破。
   * 便宜度轉分：cheapness_%B = 1 - clamp(%B, 0, 1)

2) RSI（相對強弱指標，動能超買/超賣）
         RS  = 平均漲幅 / 平均跌幅   （n 期，通常 14）
         RSI = 100 - 100 / (1 + RS)   ∈ [0, 100]
   * 便宜度轉分（RSI<=30 超賣→1，RSI>=70 超買→0）：
         cheapness_RSI = clamp( (70 - RSI) / (70 - 30), 0, 1 )

    technical_score = w_%B * cheapness_%B + w_RSI * cheapness_RSI

邊界防禦（對照 CLAUDE.md §1 / §4.6）
-----------------------------------
* upper == lower（無波動、通道寬度為 0）：%B 分母為 0。→ 丟棄 %B 子分量、
  改用 RSI 單獨計分並重新歸一化，帶旗標 band_degenerate=True（不硬填 0）。
* RSI 超出 [0,100]（上游異常）：clamp 回合法域並顯式標記 rsi_out_of_range。
* close/RSI 為 NaN/Inf 或 close<=0：視為無效資料 → 對應子分量停用；全停用則 unavailable。
"""

from __future__ import annotations

import math

from config import (
    CHIP_SCALE_LOTS,
    KD_OVERBOUGHT,
    KD_OVERSOLD,
    RSI_MAX,
    RSI_MIN,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    TECH_SUBWEIGHTS,
)

from .contracts import AgentVerdict, TechnicalSnapshot
from .numerics import clamp, isclose, linear_map

AGENT_NAME = "TechnicalAgent"


def _ma_align_score(snap: TechnicalSnapshot) -> float | None:
    """均線排列 [0,1]：命中數/3，命中 = {close>MA20, close>MA60, MA20>MA60}（趨勢強度）。

    MA20/MA60 任一缺（舊 stock.db / ETF）或非有限 → None（不計入，重新歸一化）。
    """
    if snap.ma20 is None or snap.ma60 is None:
        return None
    if not (math.isfinite(snap.close) and math.isfinite(snap.ma20) and math.isfinite(snap.ma60)):
        return None
    hits = (snap.close > snap.ma20) + (snap.close > snap.ma60) + (snap.ma20 > snap.ma60)
    return hits / 3.0


def _kd_score(snap: TechnicalSnapshot) -> float | None:
    """KD [0,1] = 0.5·(K>D 黃金交叉) + 0.5·(K 低檔空間)。K/D 缺或非有限 → None。"""
    if snap.kd_k is None or snap.kd_d is None:
        return None
    if not (math.isfinite(snap.kd_k) and math.isfinite(snap.kd_d)):
        return None
    cross = 1.0 if snap.kd_k > snap.kd_d else 0.0
    room = linear_map(snap.kd_k, KD_OVERBOUGHT, KD_OVERSOLD, 0.0, 1.0)  # K 低 → 空間大 → 高分
    return 0.5 * cross + 0.5 * room


def _chip_score(snap: TechnicalSnapshot) -> float | None:
    """三大法人籌碼 [0,1] = 0.5 + 0.5·tanh(淨張 / CHIP_SCALE_LOTS)。買超>0.5 / 賣超<0.5 / 飽和。

    total_net_lots 缺（舊 stock.db / ETF）或非有限 → None（不計入）。
    """
    lots = snap.total_net_lots
    if lots is None or not math.isfinite(lots):
        return None
    return 0.5 + 0.5 * math.tanh(lots / CHIP_SCALE_LOTS)


class TechnicalAnalysisAgent:
    """技術線型專家。"""

    def evaluate(self, snap: TechnicalSnapshot | None) -> AgentVerdict:
        if snap is None:
            return AgentVerdict.unavailable(AGENT_NAME, "無技術面資料（stock.db 查無或缺值）")

        diagnostics: dict = {"stock_id": snap.stock_id, "as_of": snap.as_of}

        # ---------- %B 子分量 ----------
        cheap_pctb: float | None = None
        percent_b: float | None = None
        band_width = snap.upper_band - snap.lower_band
        if not math.isfinite(snap.close) or snap.close <= 0:
            diagnostics["close_invalid"] = True
        elif not (math.isfinite(snap.upper_band) and math.isfinite(snap.lower_band)):
            diagnostics["band_invalid"] = True
        elif band_width <= 0 or isclose(band_width, 0.0):
            # 零寬度通道（無波動）：%B 無定義，丟棄此子分量。
            diagnostics["band_degenerate"] = True
        else:
            percent_b = (snap.close - snap.lower_band) / band_width
            cheap_pctb = 1.0 - clamp(percent_b, 0.0, 1.0)
            diagnostics["percent_b"] = round(percent_b, 4)

        # ---------- RSI 子分量 ----------
        cheap_rsi: float | None = None
        rsi_used = snap.rsi
        if not math.isfinite(rsi_used):
            diagnostics["rsi_invalid"] = True
        else:
            if rsi_used < RSI_MIN or rsi_used > RSI_MAX:
                # 顯式 clamp + 旗標（§1：填補須顯式且帶旗標）。
                rsi_used = clamp(rsi_used, RSI_MIN, RSI_MAX)
                diagnostics["rsi_out_of_range"] = True
            cheap_rsi = linear_map(rsi_used, RSI_OVERSOLD, RSI_OVERBOUGHT, 1.0, 0.0)
            diagnostics["rsi"] = round(rsi_used, 4)

        # ---------- 加厚子分量（均線排列 / KD / 三大法人籌碼；資料由 my-stock 已 export，判斷在此）----------
        ma_score = _ma_align_score(snap)
        if ma_score is not None:
            diagnostics["ma_align"] = round(ma_score, 4)
        kd_sc = _kd_score(snap)
        if kd_sc is not None:
            diagnostics["kd"] = round(kd_sc, 4)
        chip_sc = _chip_score(snap)
        if chip_sc is not None:
            diagnostics["chip"] = round(chip_sc, 4)

        # ---------- 融合（缺子分量則重新歸一化）----------
        parts: list[tuple[float, float]] = []  # (weight, subscore)
        for key, sub in (
            ("percent_b", cheap_pctb),
            ("rsi", cheap_rsi),
            ("ma_align", ma_score),
            ("kd", kd_sc),
            ("chip", chip_sc),
        ):
            if sub is not None:
                parts.append((TECH_SUBWEIGHTS[key], sub))

        if not parts:
            return AgentVerdict.unavailable(
                AGENT_NAME, f"技術指標全數無效：{diagnostics}"
            )

        total_w = sum(w for w, _ in parts)
        score = sum(w * s for w, s in parts) / total_w
        score = clamp(score, 0.0, 1.0)

        regime = self._classify_regime(percent_b, cheap_rsi, rsi_used)
        diagnostics["regime"] = regime
        diagnostics["subcomponents_used"] = len(parts)

        return AgentVerdict(
            agent=AGENT_NAME,
            available=True,
            score=round(score, 4),
            reason=self._build_reason(regime, percent_b, snap.rsi, diagnostics),
            diagnostics=diagnostics,
        )

    @staticmethod
    def _classify_regime(
        percent_b: float | None, cheap_rsi: float | None, rsi_used: float
    ) -> str:
        oversold = (percent_b is not None and percent_b <= 0.0) or (
            math.isfinite(rsi_used) and rsi_used <= RSI_OVERSOLD
        )
        overbought = (percent_b is not None and percent_b >= 1.0) or (
            math.isfinite(rsi_used) and rsi_used >= RSI_OVERBOUGHT
        )
        if oversold and not overbought:
            return "oversold_cheap"
        if overbought and not oversold:
            return "overbought_expensive"
        return "neutral"

    @staticmethod
    def _build_reason(
        regime: str, percent_b: float | None, rsi: float, diagnostics: dict
    ) -> str:
        label = {
            "oversold_cheap": "超賣便宜區（利於買進）",
            "overbought_expensive": "超買昂貴區（追高風險）",
            "neutral": "中性位階",
        }[regime]
        pb_txt = "N/A" if percent_b is None else f"{percent_b:.2f}"
        note = ""
        if diagnostics.get("band_degenerate"):
            note += "；通道零寬度已改用 RSI 單指標"
        if diagnostics.get("rsi_out_of_range"):
            note += "；RSI 超界已 clamp"
        return f"技術位階：{label}（%B={pb_txt}, RSI={rsi:.1f}）{note}"
