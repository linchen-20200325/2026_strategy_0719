"""watchlist.py — 觀察清單（要掃描的標的）+ DB 路徑設定。

WatchItem 帶每個標的的:台股代號、連動美股、新聞關鍵字、目前權重/上限/Sharpe。
（權重/Sharpe 為投組現況,來自呼叫端,非三庫資料。實務可改接 Google Sheet 政策。）

DB 路徑由環境變數提供,避免把絕對路徑寫死在程式（部署到 NAS / server 皆同）:
    STOCK_DB / FUND_DB / NEWS_DB
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..contracts import PortfolioState


@dataclass(frozen=True)
class WatchItem:
    tw_stock_id: str
    us_stock_id: str
    keywords: tuple[str, ...]
    current_weight_ratio: float
    max_weight_ratio: float = 0.20
    sharpe: float | None = None

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
