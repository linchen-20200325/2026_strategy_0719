"""watchlist.py — 觀察清單（要掃描的標的）+ DB 路徑設定。

WatchItem 帶每個標的的:台股代號、連動美股、新聞關鍵字、目前權重/上限/Sharpe。
（權重/Sharpe 為投組現況,來自呼叫端,非三庫資料。實務可改接 Google Sheet 政策。）

DB 路徑由環境變數提供,避免把絕對路徑寫死在程式（部署到 NAS / server 皆同）:
    STOCK_DB / FUND_DB / NEWS_DB
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from config import DEFAULT_MAX_WEIGHT_RATIO, DEFAULT_WEIGHT_RATIO

from ..contracts import PortfolioState

# pandas 只在下方兩個 UI 編輯表 helper 用到，故**惰性 import**（放函式內）——
# 讓「只要 WatchItem」的輕量 caller（NAS webhook bot）不必安裝 pandas（NAS 零安裝部署）。
# 型別註解需要 pd 這個名字,故僅在 TYPE_CHECKING 期 import（runtime 不載入）。
if TYPE_CHECKING:
    import pandas as pd

# UI 編輯表欄位（st.data_editor 用;為顯示標籤,轉換邏輯集中在本檔）
DF_COLUMNS = ["類別", "代號", "連動美股/基金", "新聞關鍵字", "權重", "Sharpe"]


@dataclass(frozen=True)
class WatchItem:
    tw_stock_id: str
    us_stock_id: str
    keywords: tuple[str, ...]
    current_weight_ratio: float
    max_weight_ratio: float = DEFAULT_MAX_WEIGHT_RATIO
    sharpe: float | None = None
    category: str = "台股"        # 台股 / ETF / 基金（供 UI 分組;不影響 pipeline 計算）

    def portfolio_state(self) -> PortfolioState:
        return PortfolioState(
            current_weight_ratio=self.current_weight_ratio,
            max_weight_ratio=self.max_weight_ratio,
            sharpe=self.sharpe,
        )


# 範例清單（請替換為你的實際持股/觀察組合）。
DEMO_WATCHLIST: tuple[WatchItem, ...] = (
    WatchItem("2330", "NVDA", ("台積電", "半導體", "TSMC"), 0.10, 0.20, 1.4),
    WatchItem("2454", "AMD", ("聯發科", "半導體"), 0.12, 0.20, 1.1),
)


def load_db_paths(*, allow_demo: bool = False) -> dict[str, str]:
    """從環境變數讀三個 DB 路徑;缺任一個即 raise（Fail-Loud）。"""
    keys = {"stock_db": "STOCK_DB", "fund_db": "FUND_DB", "news_db": "NEWS_DB"}
    missing = [env for env in keys.values() if not os.environ.get(env)]
    if missing:
        hint = "（或用 --demo 跑示範資料）" if allow_demo else ""
        raise OSError(f"缺少環境變數 {missing}，請設定三個 DB 路徑{hint}")
    return {pkey: os.environ[env] for pkey, env in keys.items()}


def watchlist_to_df(items: list[WatchItem] | tuple[WatchItem, ...]) -> pd.DataFrame:
    """WatchItem 清單 → 編輯表 DataFrame（供 st.data_editor）。"""
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "類別": it.category,
                "代號": it.tw_stock_id,
                "連動美股/基金": it.us_stock_id,
                "新聞關鍵字": ",".join(it.keywords),
                "權重": it.current_weight_ratio,
                "Sharpe": it.sharpe,
            }
            for it in items
        ],
        columns=DF_COLUMNS,
    )


def watchlist_from_df(df: pd.DataFrame) -> list[WatchItem]:
    """編輯表 DataFrame → WatchItem 清單。空代號列略過;缺值套用安全預設（不炸）。"""
    import pandas as pd

    items: list[WatchItem] = []
    for _, row in df.iterrows():
        code = str(row.get("代號", "") or "").strip()
        if not code:
            continue  # 空列/未填代號 → 略過
        kw_raw = str(row.get("新聞關鍵字", "") or "").replace("，", ",")
        keywords = tuple(k.strip() for k in kw_raw.split(",") if k.strip())

        weight_raw = row.get("權重")
        weight = (
            DEFAULT_WEIGHT_RATIO
            if weight_raw is None or pd.isna(weight_raw)
            else float(weight_raw)
        )

        sharpe_raw = row.get("Sharpe")
        sharpe = None if sharpe_raw is None or pd.isna(sharpe_raw) else float(sharpe_raw)

        items.append(
            WatchItem(
                tw_stock_id=code,
                us_stock_id=str(row.get("連動美股/基金", "") or "").strip(),
                keywords=keywords,
                current_weight_ratio=weight,
                sharpe=sharpe,
                category=str(row.get("類別", "台股") or "台股").strip(),
            )
        )
    return items
