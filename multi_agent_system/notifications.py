"""notifications.py — 通知抽象層（核心層,**無 streamlit 依賴**,供 cron / CLI 使用）。

`Notifier` 介面統一「把決策推出去」;今天有 Console(CLI/cron),
dashboard 內的 toast 在 ui/notify.py(需 streamlit),LINE 推播在 line_push.py(LineNotifier / LinePusher)。

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

# 以 name(str) 對照的鏡射表 + 查表函式。理由:Streamlit `@st.cache_resource` 熱重載會讓
# 「快取的 orchestrator 產出的舊 Action class」與「重載後 dict 的新 Action class」身分不一致,
# 直接 `ACTION_EMOJI[action]` 會 KeyError（enum 以身分為 key）。改以 name 對照即穩健。
_EMOJI_BY_NAME: dict[str, str] = {a.name: e for a, e in ACTION_EMOJI.items()}


def emoji_for(action: Action) -> str:
    """行動 → 交通號誌 emoji;以 name 對照,enum 身分不一致亦不炸（未知 → ⬜）。"""
    return _EMOJI_BY_NAME.get(getattr(action, "name", ""), "⬜")


def should_notify(decision: FinalDecision) -> bool:
    """只在有明確買賣傾向時通知(Hold / abstain 不推)。"""
    return not decision.abstained and decision.action != Action.HOLD


def format_notification(decision: FinalDecision) -> str:
    """一行式通知文字(emoji + 標的 + 行動 + 分數 + 風控旗標)。"""
    score = "N/A" if decision.final_score is None else f"{decision.final_score:.3f}"
    tag = "（🚨風控減碼）" if decision.risk_control_triggered else ""
    return (
        f"{emoji_for(decision.action)} [{decision.tw_stock_id}] "
        f"{decision.action.value}{tag}　Final={score}"
    )


class Notifier(Protocol):
    def notify(self, decision: FinalDecision) -> None: ...


class ConsoleNotifier:
    """印到 stdout(CLI / cron 用)。"""

    def notify(self, decision: FinalDecision) -> None:
        if should_notify(decision):
            print(format_notification(decision))


# LINE 推播的實作在 line_push.py（LineNotifier / LinePusher）;
# 放獨立檔避免與本模組的純介面混雜,且 line_push 需 import 本模組的 helper（單向,不循環）。
