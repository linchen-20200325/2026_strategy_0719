"""test_ui_apptest.py — 以 Streamlit AppTest 無頭驗證 render 元件不炸。"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def _panel_app() -> None:
    """在 AppTest 內建構決策並渲染完整面板 + 通知中心。"""
    from multi_agent_system.contracts import Action, AgentVerdict, FinalDecision
    from multi_agent_system.ui import render_decision_panel, render_notification_center

    def mk(stock, action, score, *, abstained=False, risk=False):
        verdicts = {
            "macro": AgentVerdict("MacroAgent", True, 0.80, "總經健康", {}),
            "technical": AgentVerdict("TechnicalAgent", True, 0.98, "超賣便宜", {}),
            "allocation": AgentVerdict(
                "AllocationAgent", True, 0.70, "配置正常", {"risk_control_triggered": risk}
            ),
        }
        return FinalDecision(stock, action, score, abstained, risk, verdicts, "rationale")

    render_decision_panel(mk("2330", Action.STRONG_BUY, 0.87))
    render_notification_center(
        [
            mk("2330", Action.STRONG_BUY, 0.87),
            mk("2454", Action.STRONG_SELL, 0.06, risk=True),
            mk("9999", Action.HOLD, None, abstained=True),
        ]
    )


def test_panel_and_center_render_without_exception():
    at = AppTest.from_function(_panel_app).run()
    # at.exception 為 ElementList；有例外時非空。附 repr 便於除錯。
    assert not at.exception, list(at.exception)
    # 徽章 + 通知中心會產生 markdown 元素
    assert len(at.markdown) > 0


def test_standalone_dashboard_runs():
    at = AppTest.from_file("dashboard.py").run(timeout=30)
    assert not at.exception, list(at.exception)
    assert at.title[0].value.startswith("🧠")
