"""multi_agent_system.ui — Streamlit 視覺化 / 通知元件層。

⚠️ 本子套件依賴 streamlit + altair;core（agents / 決策）**不** import 本層,
   保持核心可在無 UI 環境（cron / 測試）獨立運行（對照既有 dashboard 的分層硬規則）。

一鍵嵌入既有 dashboard:
    from multi_agent_system.ui import render_cycle_result
    render_cycle_result(orchestrator.run_once(request))
"""

from __future__ import annotations

from .components import (
    render_cycle_result,
    render_decision_panel,
    render_mock_order,
    render_notification_center,
    render_provenance,
    render_score_breakdown,
    render_signal_badge,
)
from .notify import (
    ConsoleNotifier,
    LineNotifier,
    Notifier,
    StreamlitToastNotifier,
    format_notification,
    should_notify,
)
from .theme import DEFAULT_PALETTE, Palette
from .view_model import ActionVisual, BreakdownRow, action_visual, score_breakdown

__all__ = [
    # render
    "render_decision_panel",
    "render_cycle_result",
    "render_signal_badge",
    "render_score_breakdown",
    "render_provenance",
    "render_mock_order",
    "render_notification_center",
    # notify
    "Notifier",
    "ConsoleNotifier",
    "StreamlitToastNotifier",
    "LineNotifier",
    "should_notify",
    "format_notification",
    # theme / view-model
    "Palette",
    "DEFAULT_PALETTE",
    "ActionVisual",
    "BreakdownRow",
    "action_visual",
    "score_breakdown",
]
