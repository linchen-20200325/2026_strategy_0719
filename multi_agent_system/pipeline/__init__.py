"""multi_agent_system.pipeline — 排程批次執行（cron / GitHub Actions）。

無 streamlit 依賴,供定時任務讀三庫 → 跑 agents → 通知。

**惰性載入（PEP 562）**：只有被存取到的名字才 import 對應子模組。這讓「只要 `WatchItem`」的
輕量 caller（NAS webhook bot）**不必連帶載入 `runner`（→ 各 agent → numpy/pandas）** ——
NAS 零安裝部署。cron 存取 `PipelineRunner` 等時才實際載入 `runner`。
"""

from __future__ import annotations

__all__ = [
    "WatchItem",
    "DEMO_WATCHLIST",
    "load_db_paths",
    "watchlist_to_df",
    "watchlist_from_df",
    "check_freshness",
    "FreshnessReport",
    "DbFreshness",
    "PipelineRunner",
    "RunReport",
    "bullish_ranked",
    "build_request",
]

# 公開名 → 所在子模組（相對）。存取到才 import（見 __getattr__）。
# 文字渲染（format_*/summarize）住 render_text，不由 pipeline re-export（V2/V3/V4 收尾）。
_LAZY = {
    "WatchItem": "watchlist",
    "DEMO_WATCHLIST": "watchlist",
    "load_db_paths": "watchlist",
    "watchlist_to_df": "watchlist",
    "watchlist_from_df": "watchlist",
    "check_freshness": "freshness",
    "FreshnessReport": "freshness",
    "DbFreshness": "freshness",
    "PipelineRunner": "runner",
    "RunReport": "runner",
    "bullish_ranked": "runner",
    "build_request": "runner",
}


def __getattr__(name: str):
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(f".{mod}", __name__), name)
    globals()[name] = value  # 快取：之後直接命中,不再走 __getattr__
    return value


def __dir__():
    return sorted(__all__)
