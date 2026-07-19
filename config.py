"""config.py — 全系統唯一真相源 (Single Source of Truth, SSOT)。

本檔集中定義所有「魔術數字」：融合權重、行動門檻、各專家的統計/金融參數。
任一常數只在此定義一次；其他模組一律 `from config import ...`，
嚴禁在 agent 內 inline 貼字面值（對照使用者三個 dashboard 專案 CLAUDE.md §3.3 反捏造原則）。

命名規範：變數名編碼單位（`_pct` 百分點 / `_ratio` 比例小數 / `_rate` 年化利率），
避免「百分比 vs 小數」的 100x 誤差。
"""

from __future__ import annotations

# =============================================================================
# 1. 決策融合 (Strategy Agent) — 三專家加權
# =============================================================================
# 使用者指定權重：總經 30% / 技術 50% / 配置 20%（總和必為 1.0）。
FUSION_WEIGHTS: dict[str, float] = {
    "macro": 0.30,
    "technical": 0.50,
    "allocation": 0.20,
}

# Final Score ∈ [0,1] → 五大交易行動的「含下界」門檻。
# score >= STRONG_BUY_MIN            -> 強烈買進 Strong Buy
# ADD_MIN     <= score < STRONG_BUY_MIN -> 適度加碼 Add
# HOLD_MIN    <= score < ADD_MIN        -> 持股觀望 Hold
# REDUCE_MIN  <= score < HOLD_MIN       -> 適度減碼 Reduce
# score < REDUCE_MIN                 -> 強烈賣出 Strong Sell
STRONG_BUY_MIN: float = 0.80
ADD_MIN: float = 0.60
HOLD_MIN: float = 0.40
REDUCE_MIN: float = 0.20

# =============================================================================
# 2. 總經專家 (Macro Agent) — 系統性風險
# =============================================================================
# 總經健康度 = 三個子分量加權（皆映射至 [0,1]，1=最健康）：
#   殖利率曲線 (yield curve) / 通膨 (CPI) / 市場情緒 (news sentiment)
# 子權重總和必為 1.0；若 news sentiment 缺席，於 agent 內動態重新歸一化（並帶旗標）。
MACRO_SUBWEIGHTS: dict[str, float] = {
    "curve": 0.45,
    "cpi": 0.35,
    "sentiment": 0.20,
}

# --- 殖利率曲線 (10Y-2Y spread, 單位：百分點) ---
# 曲線倒掛 (spread <= 0) 為歷史上最可靠的衰退領先指標之一 (Estrella & Mishkin, 1996)。
YIELD_INVERSION_PCT: float = 0.0    # spread <= 0 -> 曲線分量 = 0（最大壓力）
YIELD_HEALTHY_PCT: float = 1.5      # spread >= 1.5 -> 曲線分量 = 1（完全健康）

# --- 通膨 (CPI YoY, 單位：百分點) ---
CPI_TARGET_PCT: float = 2.0         # <= 2% (Fed 目標) -> CPI 分量 = 1
CPI_HOT_PCT: float = 5.0            # >= 5% (過熱) -> CPI 分量 = 0

# =============================================================================
# 3. 技術線型專家 (Technical Agent) — 布林通道 + RSI
# =============================================================================
# 技術面得分方向：越「便宜/超賣」得分越高（利於買進）；越「昂貴/超買」得分越低。
TECH_SUBWEIGHTS: dict[str, float] = {
    "percent_b": 0.50,   # 布林 %B 位階
    "rsi": 0.50,         # RSI 動能
}

RSI_MIN: float = 0.0
RSI_MAX: float = 100.0
RSI_OVERSOLD: float = 30.0    # RSI <= 30 視為超賣（便宜）
RSI_OVERBOUGHT: float = 70.0  # RSI >= 70 視為超買（昂貴）

# 布林通道標準差倍數 k（Bollinger 原始設定 n=20, k=2）。僅供文件/校驗參考，
# 本系統假設上/下軌已由 my-stock-dashboard 上游算好，不在此重算。
BBAND_STD_MULT: float = 2.0

# =============================================================================
# 4. 資產配置專家 (Allocation Agent) — MPT / Sharpe / 集中度風控
# =============================================================================
# Sharpe 線性映射區間：<=0 視為無風險調整後超額報酬 -> 0 分；>=2 為優異 -> 1 分。
SHARPE_FLOOR: float = 0.0
SHARPE_CAP: float = 2.0

# 單一持股權重上限（比例，非百分數）。超過即觸發強制風控減碼。
DEFAULT_MAX_WEIGHT_RATIO: float = 0.20

# 追蹤標的「目前權重」的預設值（比例，非百分數）：呼叫端/使用者未指定時套用。
# SSOT：watchlist / subscribers / CLI / webhook bot 一律引用此常數，勿再散落字面值。
DEFAULT_WEIGHT_RATIO: float = 0.10

# 觸發集中度風控時，配置得分被強制壓在此上限以下（拖低 Final Score → 減碼/賣出）。
RISK_CONTROL_SCORE_CAP: float = 0.25

# Sharpe 自算所需（當呼叫端傳入報酬序列而非現成 Sharpe 時）。
RF_ANNUAL_RATE: float = 0.02        # 年化無風險利率（約當短天期公債）
TRADING_DAYS_PER_YEAR: int = 252    # 交易日年化因子（非 365 日曆日）

# =============================================================================
# 5. 資料代理人 (Data Agent) — 新聞 / 情緒
# =============================================================================
NEWS_LOOKBACK_DAYS: int = 7          # 抓取過去 N 天新聞
# 假設上游 sentiment_score ∈ [-1, 1]（-1 極空 / +1 極多）；讀取時 clamp 防呆。
SENTIMENT_RAW_MIN: float = -1.0
SENTIMENT_RAW_MAX: float = 1.0

# =============================================================================
# 6. 數值容差（浮點比較一律用容差，禁止 ==）
# =============================================================================
FLOAT_ABS_TOL: float = 1e-9
FLOAT_REL_TOL: float = 1e-9


def _validate_config() -> None:
    """啟動時自我檢查：權重歸一化 + 門檻單調遞增。Fail Loud。"""
    import math

    for name, weights in (
        ("FUSION_WEIGHTS", FUSION_WEIGHTS),
        ("MACRO_SUBWEIGHTS", MACRO_SUBWEIGHTS),
        ("TECH_SUBWEIGHTS", TECH_SUBWEIGHTS),
    ):
        total = math.fsum(weights.values())
        if not math.isclose(total, 1.0, abs_tol=FLOAT_ABS_TOL):
            raise ValueError(f"[config] {name} 權重總和 {total!r} != 1.0，違反歸一化契約")

    cutoffs = [REDUCE_MIN, HOLD_MIN, ADD_MIN, STRONG_BUY_MIN]
    if cutoffs != sorted(cutoffs) or len(set(cutoffs)) != len(cutoffs):
        raise ValueError(f"[config] 行動門檻必須嚴格遞增：{cutoffs}")
    if not (0.0 < REDUCE_MIN and STRONG_BUY_MIN < 1.0):
        raise ValueError("[config] 行動門檻必須落在開區間 (0, 1)")


_validate_config()
