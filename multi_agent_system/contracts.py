"""contracts.py — 系統內部資料契約 (typed dataclasses + enums)。

所有 agent 之間傳遞的物件都在此定義，達成「邊界契約 (Schema)」：
每個欄位帶單位/來源，關鍵資料帶血緣 (provenance: source / fetched_at / as_of)。

設計原則（對照使用者 CLAUDE.md）：
* Fail Loud：資料缺席時以 `available=False` + 明確 reason 呈現，不塞假值。
* Provenance：跨庫抓來的每一段都帶 source 與時間戳。
* 不可變：對外快照一律 frozen dataclass，避免下游誤改。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import DEFAULT_MAX_WEIGHT_RATIO


def utc_now() -> datetime:
    """統一以 UTC 產生 fetched_at（顯示時再轉本地 UTC+8）。"""
    return datetime.now(timezone.utc)


class Action(enum.Enum):
    """五大交易行動訊號（依 Final Score 由高到低）。"""

    STRONG_BUY = "強烈買進 (Strong Buy)"
    ADD = "適度加碼 (Add)"
    HOLD = "持股觀望 (Hold)"
    REDUCE = "適度減碼 (Reduce)"
    STRONG_SELL = "強烈賣出 (Strong Sell)"

    @property
    def tone(self) -> str:
        """三態情緒 SSOT：bullish(SB/ADD) / neutral(HOLD) / bearish(REDUCE/SS)。

        利多/中性/利空 分類的唯一來源（view_model / market_digest / is_bullish 皆由此衍生，
        避免同一分類在多處重刻而漂移）。
        """
        if self in (Action.STRONG_BUY, Action.ADD):
            return "bullish"
        if self in (Action.REDUCE, Action.STRONG_SELL):
            return "bearish"
        return "neutral"

    @property
    def is_bullish(self) -> bool:
        return self.tone == "bullish"


# 由多頭到空頭的排序（供風控 hard-override 比較「不得比 REDUCE 更偏多」）。
ACTION_BULLISH_ORDER: tuple[Action, ...] = (
    Action.STRONG_SELL,
    Action.REDUCE,
    Action.HOLD,
    Action.ADD,
    Action.STRONG_BUY,
)


# ------------------------------------------------------------------ 資料層快照

@dataclass(frozen=True)
class TechnicalSnapshot:
    """個股最新一期技術面（來源：my-stock-dashboard / stock.db）。

    核心欄（close/rsi/布林軌）為必需；**盯盤卡加料欄**（均線/KD/籌碼）為選填，
    對應舊版 stock.db（僅 6 欄）缺欄時 → None（顯示「—」，不捏造，§1 Fail Loud）。
    單位鐵則：均線=元、KD=0~100 無單位、籌碼=張（賣超為負，禁止換算為金額混用）。
    """

    stock_id: str
    as_of: str                 # 資料歸屬日 (YYYY-MM-DD)，非抓取日
    close: float
    rsi: float
    upper_band: float          # 布林上軌
    lower_band: float          # 布林下軌
    ma20: float | None = None          # 20 日均線（元）
    ma60: float | None = None          # 60 日均線（元）
    kd_k: float | None = None          # KD 之 K（0~100 無單位）
    kd_d: float | None = None          # KD 之 D（0~100 無單位）
    foreign_net_lots: float | None = None   # 外資買賣超（張；賣超為負）
    trust_net_lots: float | None = None     # 投信買賣超（張）
    total_net_lots: float | None = None     # 三大法人買賣超（張＝外資＋投信＋自營）
    source: str = "stock.db"
    fetched_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class UsLinkSnapshot:
    """連動美股/基金最新一期（來源：my-Fund-dashboard / fund.db）。"""

    us_stock_id: str
    as_of: str
    close: float
    source: str = "fund.db"
    fetched_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class FinancialsSnapshot:
    """個股最新一期季報（來源：my-stock-dashboard / stock.db stock_fundamentals）。

    單位鐵則：金額欄=**千元**、eps=元、margin=%。roc_year 為**民國年**（+1911=西元）。
    金額 / margin 可缺（None → 顯示時略過該欄，不捏造，§1 Fail Loud）。
    """

    stock_id: str
    roc_year: int
    season: int
    eps: float | None
    revenue_k: float | None            # 營收（千元）
    gross_margin_pct: float | None     # 毛利率 %（gross_profit / revenue）
    net_margin_pct: float | None       # 淨利率 %（net_income / revenue）
    source: str = "stock.db:stock_fundamentals"
    fetched_at: datetime = field(default_factory=utc_now)

    @property
    def period_label(self) -> str:
        """西元年 + 季，如 '2026 Q1'（roc_year 115 → 2026）。"""
        return f"{self.roc_year + 1911} Q{self.season}"


@dataclass(frozen=True)
class NewsItem:
    """單則新聞（來源：mynews / news.db）。"""

    as_of: str
    title: str
    sentiment_score: float     # 假設 ∈ [-1, 1]


@dataclass(frozen=True)
class DataPacket:
    """資料代理人打包的標準封包（跨三庫整合結果）。"""

    tw_stock_id: str
    technical: TechnicalSnapshot | None
    us_link: UsLinkSnapshot | None
    news: tuple[NewsItem, ...]
    news_sentiment_mean: float | None   # 過去 N 天平均情緒；無新聞則 None
    news_count: int
    financials: FinancialsSnapshot | None = None   # 最新一期季報（缺 → None）
    revenue_yoy_pct: float | None = None           # 最新月營收年增率 %（缺/未落地 → None）
    warnings: tuple[str, ...] = ()      # 缺料/降級的明確告警（不靜默）
    fetched_at: datetime = field(default_factory=utc_now)

    @property
    def has_technical(self) -> bool:
        return self.technical is not None

    def to_json_dict(self) -> dict:
        """轉為可序列化 JSON 封包（datetime → ISO 字串）。"""
        def snap(obj):
            if obj is None:
                return None
            d = obj.__dict__.copy()
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            return d

        return {
            "tw_stock_id": self.tw_stock_id,
            "technical": snap(self.technical),
            "us_link": snap(self.us_link),
            "news": [snap(n) for n in self.news],
            "news_sentiment_mean": self.news_sentiment_mean,
            "news_count": self.news_count,
            "financials": snap(self.financials),
            "revenue_yoy_pct": self.revenue_yoy_pct,
            "warnings": list(self.warnings),
            "fetched_at": self.fetched_at.isoformat(),
        }


# ------------------------------------------------------------------ 專家輸出

@dataclass(frozen=True)
class AgentVerdict:
    """單一專家的評估結果。

    score ∈ [0,1]（1=最看多/最健康）；資料不足時 score=None 且 available=False。
    """

    agent: str
    available: bool
    score: float | None
    reason: str
    diagnostics: dict = field(default_factory=dict)

    @staticmethod
    def unavailable(agent: str, reason: str) -> AgentVerdict:
        return AgentVerdict(agent=agent, available=False, score=None, reason=reason)


@dataclass(frozen=True)
class MacroReading:
    """總經原始輸入（殖利率利差 + CPI + 情緒），帶血緣與模擬旗標。

    語意上為**美股 / 全球**總經（來源：my-Fund-dashboard / fund.db fred_macro）。
    """

    yield_spread_pct: float     # 10Y - 2Y，單位百分點
    cpi_yoy_pct: float          # CPI 年增率，單位百分點
    source: str
    as_of: str
    is_simulated: bool          # True = 模擬/注入值，非真實 API（Fail Loud 透明化）


@dataclass(frozen=True)
class TwMacroReading:
    """台股總經快照（來源：my-stock-dashboard / stock.db）。

    * pmi           製造業採購經理人指數（指數點位，榮枯線 50；非百分比）。
    * foreign_net_yi 外資買賣超（單位 **億元**，賣超為負；禁止與「張」混用）。

    兩指標**各自獨立可缺**：查無 → None（顯示「資料不足」，不捏造，§1 Fail Loud），
    不因單一指標缺席而讓整段台股情勢消失。
    """

    pmi: float | None
    pmi_as_of: str | None       # PMI 歸屬月（YYYY-MM-DD）
    foreign_net_yi: float | None
    foreign_as_of: str | None   # 外資買賣超歸屬交易日
    source: str
    is_simulated: bool = False


@dataclass(frozen=True)
class TwNightReading:
    """台股盤前訊號：台指期外資留倉 + 台指夜盤漲跌（來源：my-stock-dashboard / stock.db）。

    兩訊號各自獨立可缺（None → 該段不顯示，不捏造）：
    * foreign_fut_oi_lots  外資期貨留倉淨口數（**口**；+ 淨多 / − 淨空）。
    * night_close/chg       台指期夜盤（盤後 15:00–05:00 台灣時間）收盤 + 相對日盤收盤漲跌
      （night_chg_pts 點 / night_chg_pct %）—— 涵蓋歐美盤 → 對隔日台股開盤有領先性。
    """

    foreign_fut_oi_lots: float | None
    fut_oi_as_of: str | None
    night_close: float | None
    night_chg_pts: float | None
    night_chg_pct: float | None
    night_as_of: str | None
    source: str
    is_simulated: bool = False


@dataclass(frozen=True)
class PortfolioState:
    """資產配置專家的輸入（來自呼叫端的投組現況，非三庫資料）。"""

    current_weight_ratio: float           # 該標的目前佔投組比例（小數，0~1）
    max_weight_ratio: float               # 允許上限（小數）
    sharpe: float | None = None           # 現成 Sharpe；與 returns 二擇一
    returns: tuple[float, ...] | None = None  # 日報酬序列（供自算 Sharpe）


@dataclass(frozen=True)
class FinalDecision:
    """策略專家的決策融合輸出。"""

    tw_stock_id: str
    action: Action
    final_score: float | None             # 資料不足時 None（abstain）
    abstained: bool
    risk_control_triggered: bool
    verdicts: dict                        # {agent_name: AgentVerdict}
    rationale: str
    decided_at: datetime = field(default_factory=utc_now)

    def summary(self) -> str:
        score_txt = "N/A" if self.final_score is None else f"{self.final_score:.3f}"
        return f"[{self.tw_stock_id}] {self.action.value} | Final={score_txt}"


# ------------------------------------------------------------------ 券商 / 工作流輸出 DTO
@dataclass(frozen=True)
class OrderReceipt:
    """下單回執（券商下單結果 DTO）。is_mock=True 代表未真實成交。"""

    order_id: str
    symbol: str
    side: str            # "BUY" / "SELL"
    quantity: float
    status: str
    is_mock: bool
    placed_at: datetime = field(default_factory=utc_now)


@dataclass
class CycleResult:
    """單次工作流輸出（決策 + 資料封包 + 下單回執），供觀測。"""

    decision: FinalDecision
    packet: DataPacket
    receipt: OrderReceipt | None = None


@dataclass(frozen=True)
class WatchItem:
    """觀察清單一檔（UI 編輯表 / 訂閱清單 / pipeline 共用的核心 DTO）。"""

    tw_stock_id: str
    us_stock_id: str
    keywords: tuple[str, ...]
    current_weight_ratio: float
    max_weight_ratio: float = DEFAULT_MAX_WEIGHT_RATIO
    sharpe: float | None = None
    category: str = "台股"        # 台股 / ETF / 基金（供 UI 分組；不影響 pipeline 計算）

    def portfolio_state(self) -> PortfolioState:
        return PortfolioState(
            current_weight_ratio=self.current_weight_ratio,
            max_weight_ratio=self.max_weight_ratio,
            sharpe=self.sharpe,
        )
