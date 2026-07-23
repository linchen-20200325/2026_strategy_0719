"""multiuser.py — 個人化推播：對每位訂閱者跑他自己的清單，LINE push 逐人。

流程：SubscriberStore（userId → WatchItem）→ 每人各自 run_batch → 篩利多 →
`LinePusher(token, userId).push_text(個人化 digest)`。用 LINE **push（單一對象）逐人推**，
不是 multicast（multicast 只能把同一份發給一群人，無法一人一單）。

Fail-Loud：缺 token → raise;單一 user 推播失敗 → 記錄該人 error,不中斷其他人。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from config import today_tw

from .contracts import WatchItem
from .integration_agent import WorkflowOrchestrator
from .line_push import LinePusher, LinePushError
from .macro_providers import MacroDataProvider
from .pipeline import build_request, bullish_ranked
from .render_text import format_bullish_digest, format_watch_digest

logger = logging.getLogger("multi_agent_system.multiuser")


class _Store(Protocol):
    """只需 store 的讀取介面（避免 import 具體 store 造成循環）。"""

    def user_ids(self) -> list[str]: ...
    def get(self, user_id: str) -> list[WatchItem]: ...


@dataclass
class UserPushResult:
    user_id: str
    n_tracked: int      # 追蹤檔數
    n_bullish: int      # 其中利多檔數
    pushed: bool        # 是否成功推播
    error: str | None = None


def run_per_user_push(
    store: _Store,
    orchestrator: WorkflowOrchestrator,
    macro_provider: MacroDataProvider,
    *,
    channel_access_token: str | None,
    as_of: date | None = None,
    only_bullish: bool = True,
    full_watch: bool = False,
    dry_run: bool = False,
) -> list[UserPushResult]:
    """對每位訂閱者跑其清單並 push 個人化訊號。

    full_watch=True：推**全清單盯盤卡**（每檔判讀＋技術＋籌碼，對齊 LINE 盯盤 bot），
        即使無利多也推（每日固定收自己清單狀態）。此模式下 only_bullish 被忽略。
    full_watch=False（預設）：只推利多榜；only_bullish=True 時某人無利多 → 不推（避免洗版）。
    dry_run=True：只算不推。
    """
    if not dry_run and not channel_access_token:
        raise LinePushError("缺 LINE_CHANNEL_ACCESS_TOKEN,無法 push")

    results: list[UserPushResult] = []
    for uid in store.user_ids():
        items = store.get(uid)
        if not items:
            results.append(UserPushResult(uid, 0, 0, False))
            continue

        cycles = orchestrator.run_batch(
            [build_request(it, macro_provider, as_of=as_of) for it in items]
        )
        ranked = bullish_ranked(cycles)

        if full_watch:
            digest = format_watch_digest(
                cycles,
                day=(as_of or today_tw()).isoformat(),
            )
        else:
            if only_bullish and not ranked:
                results.append(UserPushResult(uid, len(items), 0, False))
                continue
            digest = format_bullish_digest(cycles)
        if dry_run:
            logger.info("[dry-run] %s → %d 利多\n%s", uid, len(ranked), digest)
            results.append(UserPushResult(uid, len(items), len(ranked), False))
            continue

        try:
            LinePusher(channel_access_token, uid).push_text(digest)
            results.append(UserPushResult(uid, len(items), len(ranked), True))
        except LinePushError as exc:
            logger.warning("[multiuser] %s 推播失敗：%s", uid, exc)
            results.append(UserPushResult(uid, len(items), len(ranked), False, str(exc)))
    return results
