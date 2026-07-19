"""notifications.py — 通知抽象層（核心層,**無 streamlit 依賴**,供 cron / CLI 使用）。

`Notifier` 介面統一「把決策推出去」;今天有 Console(CLI/cron),
dashboard 內的 toast 在 ui/notify.py(需 streamlit),LINE 之後接（LineNotifier 骨架）。

通知門檻:只推「可行動」訊號(非 Hold、非 abstain),避免洗版。
"""

from __future__ import annotations

from typing import Protocol

from .contracts import Action, FinalDecision

# 交通號誌 emoji（綠=買 / 黃=中性 / 橘紅=賣）,與中文標籤並用,不靠顏色單獨表意。
ACTION_EMOJI: dict[Action, str] = {
    Action.STRONG_BUY: "🟢",
    Action.ADD: "🟢",
    Action.HOLD: "🟡",
    Action.REDUCE: "🟠",
    Action.STRONG_SELL: "🔴",
}


def should_notify(decision: FinalDecision) -> bool:
    """只在有明確買賣傾向時通知(Hold / abstain 不推)。"""
    return not decision.abstained and decision.action != Action.HOLD


def format_notification(decision: FinalDecision) -> str:
    """一行式通知文字(emoji + 標的 + 行動 + 分數 + 風控旗標)。"""
    score = "N/A" if decision.final_score is None else f"{decision.final_score:.3f}"
    tag = "（🚨風控減碼）" if decision.risk_control_triggered else ""
    return (
        f"{ACTION_EMOJI[decision.action]} [{decision.tw_stock_id}] "
        f"{decision.action.value}{tag}　Final={score}"
    )


class Notifier(Protocol):
    def notify(self, decision: FinalDecision) -> None: ...


class ConsoleNotifier:
    """印到 stdout(CLI / cron 用)。"""

    def notify(self, decision: FinalDecision) -> None:
        if should_notify(decision):
            print(format_notification(decision))


class LineNotifier:
    """LINE 推播(規劃中,排在視覺化之後)。

    設計:實作與 Console 相同的 `Notifier` 介面,屆時 runner 只要換注入即可。
    尚未接線 → 明確 raise(Fail Loud),不靜默吞掉。

    接線方式(LINE Messaging API push；LINE Notify 已於 2025 停用):
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
