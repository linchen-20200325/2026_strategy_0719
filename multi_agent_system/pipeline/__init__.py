"""multi_agent_system.pipeline — 排程批次執行（cron / GitHub Actions）。

無 streamlit 依賴,供定時任務讀三庫 → 跑 agents → 通知。
"""

from __future__ import annotations

from .freshness import DbFreshness, FreshnessReport, check_freshness
from .runner import (
    PipelineRunner,
    RunReport,
    bullish_ranked,
    format_bullish_digest,
    format_run_digest,
    summarize,
)
from .watchlist import (
    DEMO_WATCHLIST,
    WatchItem,
    load_db_paths,
    watchlist_from_df,
    watchlist_to_df,
)

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
    "summarize",
    "format_run_digest",
    "bullish_ranked",
    "format_bullish_digest",
]
