"""ui/notify.py — 通知（re-export 核心 notifiers + Streamlit toast）。

核心 Notifier / ConsoleNotifier / LineNotifier 已移至 streamlit-free 的
`multi_agent_system.notifications`（cron/CLI 可用）。此處只加需要 streamlit 的 toast。
"""

from __future__ import annotations

from ..line_push import LineNotifier
from ..notifications import (
    ConsoleNotifier,
    Notifier,
    format_notification,
    should_notify,
)

__all__ = [
    "Notifier",
    "ConsoleNotifier",
    "LineNotifier",
    "StreamlitToastNotifier",
    "should_notify",
    "format_notification",
]


class StreamlitToastNotifier:
    """在 dashboard 內以 st.toast 彈出(即時通知小元件)。"""

    def notify(self, decision) -> None:
        if not should_notify(decision):
            return
        import streamlit as st

        st.toast(format_notification(decision))
