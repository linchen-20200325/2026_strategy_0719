"""config.py — 全系統唯一真相源 (Single Source of Truth, SSOT)。

本檔集中定義所有「魔術數字」：融合權重、行動門檻、各專家的統計/金融參數。
任一常數只在此定義一次；其他模組一律 `from config import ...`，
嚴禁在 agent 內 inline 貼字面值（對照使用者三個 dashboard 專案 CLAUDE.md §3.3 反捏造原則）。

命名規範：變數名編碼單位（`_pct` 百分點 / `_ratio` 比例小數 / `_rate` 年化利率），
避免「百分比 vs 小數」的 100x 誤差。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# =============================================================================
# 0. 時間 SSOT — 台灣時區 (UTC+8)
# =============================================================================
# 全系統「現在 / 今天」的唯一真相源。所有外部資料（news.db / stock.db / 夜盤 / fund.db）
# 皆以**台灣日期**戳記，但雲端 runner（GitHub Actions）為 UTC。盤前班於 23:30 UTC 觸發，
# naive date.today() 會落後台灣日期一天，使當日新聞被 [as_of-7, as_of] 日期窗濾掉（→ 無資料）。
# 跨層 as_of / 新聞窗 / freshness 一律用 today_tw()，嚴禁 naive date.today()。
TW_TZ = timezone(timedelta(hours=8))


def now_tw() -> datetime:
    """台灣當下（tz-aware datetime）。"""
    return datetime.now(TW_TZ)


def today_tw() -> date:
    """台灣今天（date）。跨層 as_of / 新聞窗 / freshness 對齊一律用此。"""
    return now_tw().date()


# =============================================================================
# 1. 決策融合 (Strategy Agent) — 三專家加權
# =============================================================================
# 融合權重（總和必為 1.0）。加入「基本面」專家後，macro:technical:allocation 仍維持
# 原 3:5:2 比例（0.24:0.40:0.16），故當某標的**無基本面資料**時，就可用專家重新歸一化
# 會精確還原成原本的 0.30/0.50/0.20 —— ETF/查無財報者行為不變，基本面只在有資料時加權。
FUSION_WEIGHTS: dict[str, float] = {
    "macro": 0.24,
    "technical": 0.40,
    "fundamental": 0.20,
    "allocation": 0.16,
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
# 2b. 基本面專家 (Fundamental Agent) — 財報品質 + 月營收動能
# =============================================================================
# 基本面得分 = 毛利率 / 淨利率 / 月營收 YoY 三分量加權（皆映射至 [0,1]，1=最佳）。
# 月營收缺（需 FINMIND_TOKEN 才落地）時於 agent 內重新歸一化（只用毛利+淨利）。
FUNDAMENTAL_SUBWEIGHTS: dict[str, float] = {
    "gross_margin": 0.30,
    "net_margin": 0.40,
    "revenue_yoy": 0.30,
}

# 各分量線性映射區間（單位 %）。x <= LOW → 0 分；x >= HIGH → 1 分；中間線性。
GROSS_MARGIN_LOW_PCT: float = 0.0
GROSS_MARGIN_HIGH_PCT: float = 40.0     # 毛利率 40% 視為優異
NET_MARGIN_LOW_PCT: float = 0.0
NET_MARGIN_HIGH_PCT: float = 20.0       # 淨利率 20% 視為優異
REVENUE_YOY_LOW_PCT: float = -20.0      # 月營收年減 20% → 0 分
REVENUE_YOY_HIGH_PCT: float = 30.0      # 月營收年增 30% → 1 分

# =============================================================================
# 3. 技術線型專家 (Technical Agent) — 布林通道 + RSI
# =============================================================================
# 技術面得分：多因子（便宜/均值回歸 + 趨勢/動能/籌碼）綜合，各子分量 [0,1]，越高越偏多。
# 全部子分量都用 my-stock 已 export 的原始欄位（close/RSI/布林/MA20/MA60/KD/三大法人籌碼）——
# 判斷在 2026 做，來源只出資料。缺欄（舊 stock.db / ETF）→ 該子分量不計、重新歸一化。
# 向後相容鐵則：percent_b 與 rsi 同權重 → 只有這兩者時歸一化後 = 原本 0.5/0.5，行為不變。
TECH_SUBWEIGHTS: dict[str, float] = {
    "percent_b": 0.20,   # 布林 %B 位階（便宜度）
    "rsi": 0.20,         # RSI 動能（便宜度）
    "ma_align": 0.25,    # 均線排列（close vs MA20 vs MA60；趨勢）
    "kd": 0.15,          # KD（黃金交叉 + 低檔空間；動能）
    "chip": 0.20,        # 三大法人買賣超（資金流向）
}

RSI_MIN: float = 0.0
RSI_MAX: float = 100.0
RSI_OVERSOLD: float = 30.0    # RSI <= 30 視為超賣（便宜）
RSI_OVERBOUGHT: float = 70.0  # RSI >= 70 視為超買（昂貴）

# KD 指標（0~100）超買/超賣，供 KD 子分量的「低檔空間」線性映射。
KD_OVERBOUGHT: float = 80.0
KD_OVERSOLD: float = 20.0
# 三大法人淨買賣超（張）正規化尺度：chip 子分量 = 0.5 + 0.5·tanh(淨張 / 此值)。
# 3000 張 ≈ 中型股「有感」單日法人買賣超；tanh 飽和 → 極端量不會爆表。
CHIP_SCALE_LOTS: float = 3000.0

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
# 5b. 市場快訊（國際情勢 + 台股 broadcast，mynews 風格）
# =============================================================================
# 新聞分類關鍵字（title/content 命中即歸類）——切「國際情勢」vs「台股」。SSOT，勿散落字面值。
INTL_NEWS_KEYWORDS: tuple[str, ...] = (
    "Fed", "聯準會", "美股", "通膨", "CPI", "PCE", "降息", "升息", "美債",
    "殖利率", "衰退", "地緣", "關稅", "OPEC", "原油", "那斯達克", "道瓊", "標普",
)
TW_MARKET_KEYWORDS: tuple[str, ...] = (
    "台股", "加權", "台積電", "外資", "櫃買", "集中市場", "護國神山",
    "半導體", "航運", "金融股", "電子股", "台幣", "融資",
)
# 情緒標籤門檻（近 N 則新聞平均 sentiment ∈ [-1,1] → 偏多/中性/偏空）。
DIGEST_SENTIMENT_BULLISH_MIN: float = 0.15   # >= → 偏多
DIGEST_SENTIMENT_BEARISH_MAX: float = -0.15  # <= → 偏空（之間為中性）
DIGEST_NEWS_TOP_N: int = 3                    # 摘要列出的頭條數上限

# 台股總經（stock.db）判讀門檻 —— 市場快訊「台股情勢」用。
# PMI（製造業採購經理人指數，指數點位，非百分比）：>= 50 擴張 / < 50 收縮（榮枯線）。
PMI_EXPANSION_LEVEL: float = 50.0
# 外資買賣超（stock.db institutional_flow，單位 億元）：sign 即語意（>0 買超 / <0 賣超），無需門檻。

# 台指夜盤漲跌分類（相對日盤收盤 %）—— 盤前「隔日開盤方向」判讀（對照 kevin801221 repo 五分類）。
NIGHT_BIG_MOVE_PCT: float = 1.0     # |chg%| >= 1.0 → 大漲 / 大跌
NIGHT_SMALL_MOVE_PCT: float = 0.2   # |chg%| >= 0.2 → 小漲 / 小跌；之間 → 持平

# 大盤判讀（market_digest.market_regime 的規則式綜合解讀，非 LLM）——
# 綜合 總經 + 台股總經 + 夜盤 + 美股/台股新聞情緒 → 各面向映射偏多度 ∈[0,1]，等權平均。
MARKET_REGIME_BULL_MIN: float = 0.60   # 綜合偏多度 >= → 偏多
MARKET_REGIME_BEAR_MAX: float = 0.40   # <= → 偏空；之間 → 中性
PMI_REGIME_SPAN: float = 5.0           # PMI 以榮枯線 ±SPAN 映射偏多度（45→0 / 55→1）

# session 顯示標籤（runner 彙整 digest 與 market digest 共用 → SSOT，勿各自寫 map）。
SESSION_LABELS: dict[str, str] = {"morning": "早盤前", "afternoon": "收盤後"}

# 大盤判讀語意標籤（market_regime 產出）—— ledger 對帳分類與 _regime_word 的**唯一** SSOT，
# 勿散落字面值（判讀字串一改、對帳分類就對不上，故此處集中定義）。
REGIME_LABEL_BULL: str = "偏多"
REGIME_LABEL_NEUTRAL: str = "中性"
REGIME_LABEL_BEAR: str = "偏空"

# =============================================================================
# 5c. 判讀 Ledger（forward-test 對帳）
# =============================================================================
# 把每次「大盤判讀」存檔，T+N 交易日後用 market_index 實際 open-to-open 報酬對帳，
# 產出命中率 —— 系統自我 track record。forward-test（只評分過去、結構上不可能 lookahead）。
LEDGER_HORIZON_TRADING_DAYS: int = 20   # 對帳前瞻視窗（交易日，≈1 個月；market_index 每列=一交易日）
# 命中容差 —— **每日** no-move 尺度（報酬比例非百分點，§4.1）。對帳時由 report 端
# `horizon_band()` × √horizon 放大到視窗尺度（如 20 日 → 0.5%×√20 ≈ 2.24%），才是「一個月
# 幾乎沒動」的合理閾值；否則日尺度容差硬套月報酬 → 中性桶結構性 0% 命中（band bug）。
LEDGER_HIT_BAND_RATIO: float = 0.005    # 0.5% / 日
# 命中率統計「可信樣本」下限：低於此值報表附「樣本少，僅供參考」旗標（防過早下結論）。
LEDGER_MIN_MEANINGFUL_SAMPLE: int = 30

# 機械式跟單淨值（ledger 延伸）—— 各判讀對應市場曝險（比例）。
# 預設 long-only 防禦（偏多才進場、其餘空手）；改 long-short 只需把偏空設 -1.0。
LEDGER_EXPOSURE: dict[str, float] = {
    REGIME_LABEL_BULL: 1.0,
    REGIME_LABEL_NEUTRAL: 0.0,
    REGIME_LABEL_BEAR: 0.0,
}
# 換手成本（比例，來回）：曝險改變時扣除，誠實反映 churn。
# 台股手續 0.1425%×2 + 證交稅 0.3% ≈ 0.6%。
LEDGER_SWITCH_COST_RATIO: float = 0.006

# regime 標籤（判讀當下市場狀態，供「分 regime 對帳」）——
# MVP 第一軸：殖利率曲線。spread <= YIELD_INVERSION_PCT(0) → 倒掛（late-cycle/風險），否則正常。
REGIME_YIELD_INVERTED: str = "倒掛"
REGIME_YIELD_NORMAL: str = "正常"
REGIME_UNTAGGED: str = "未標記"   # 舊判讀列（無 regime 欄）讀入時的預設

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
        ("FUNDAMENTAL_SUBWEIGHTS", FUNDAMENTAL_SUBWEIGHTS),
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
