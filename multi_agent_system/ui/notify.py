"""notify.py — 通知抽象層。

`Notifier` 介面統一「把決策推出去」的動作；今天先有 Console / Streamlit toast，
未來 LINE 推播只要實作同一介面即可插入(骨架已備,見 LineNotifier)。

通知門檻:只推「可行動」訊號(非 Hold、非 abstain),避免洗版。
"""

from __future__ import annotations

from typing import Protocol

from ..contracts import Action, FinalDecision


def should_notify(decision: FinalDecision) -> bool:
    """只在有明確買賣傾向時通知(Hold / abstain 不推)。"""
    return not decision.abstained and decision.action != Action.HOLD


def format_notification(decision: FinalDecision) -> str:
    """一行式通知文字(emoji + 標的 + 行動 + 分數 + 風控旗標)。"""
    from .view_model import action_visual, final_score_text

    av = action_visual(decision.action)
    tag = "（🚨風控減碼）" if decision.risk_control_triggered else ""
    return f"{av.emoji} [{decision.tw_stock_id}] {av.label}{tag}　Final={final_score_text(decision)}"


class Notifier(Protocol):
    def notify(self, decision: FinalDecision) -> None: ...


class ConsoleNotifier:
    """印到 stdout(CLI / cron 用)。"""

    def notify(self, decision: FinalDecision) -> None:
        if should_notify(decision):
            print(format_notification(decision))


class StreamlitToastNotifier:
    """在 dashboard 內以 st.toast 彈出(即時通知小元件)。"""

    def notify(self, decision: FinalDecision) -> None:
        if not should_notify(decision):
            return
        import streamlit as st

        st.toast(format_notification(decision))


class LineNotifier:
    """LINE 推播(規劃中,之後再接)。

    設計:實作與 Console/Toast 相同的 `Notifier` 介面,屆時 orchestrator 只要換注入即可。
    尚未接線 → 明確 raise(Fail Loud),不靜默吞掉。

    未來接線方式(LINE Messaging API push；LINE Notify 已於 2025 停用):
        POST https://api.line.me/v2/bot/message/push
        Header: Authorization: Bearer <channel_access_token>
        Body:   {"to": <userId>, "messages": [{"type": "text", "text": <文字>}]}
    """

    def __init__(self, channel_access_token: str | None = None, to: str | None = None) -> None:
        self.channel_access_token = channel_access_token
        self.to = to

    def notify(self, decision: FinalDecision) -> None:  # pragma: no cover - 尚未接線
        if not should_notify(decision):
            return
        raise NotImplementedError(
            "LineNotifier 尚未接線(依需求排在視覺化之後)。接線時於此 POST "
            "LINE Messaging API /v2/bot/message/push，文字用 format_notification(decision)。"
        )
