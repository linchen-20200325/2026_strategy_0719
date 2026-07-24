"""test_ui_view_model.py — 純視覺轉換層（不需 streamlit）。"""

from __future__ import annotations

import pytest

from multi_agent_system.contracts import Action, AgentVerdict, FinalDecision
from multi_agent_system.ui import notify
from multi_agent_system.ui import view_model as vm
from multi_agent_system.ui.theme import DEFAULT_PALETTE


def _decision(action, score, *, abstained=False, risk=False, tech_score=0.9):
    verdicts = {
        "macro": AgentVerdict("MacroAgent", True, 0.8, "總經 ok", {}),
        "technical": AgentVerdict("TechnicalAgent", True, tech_score, "技術 ok", {}),
        "allocation": AgentVerdict(
            "AllocationAgent", True, 0.7, "配置 ok", {"risk_control_triggered": risk}
        ),
    }
    return FinalDecision("2330", action, score, abstained, risk, verdicts, "rationale")


def test_action_visual_colors_match_palette():
    assert vm.action_visual(Action.STRONG_BUY).hex == DEFAULT_PALETTE.strong_buy
    assert vm.action_visual(Action.HOLD).hex == DEFAULT_PALETTE.hold
    assert vm.action_visual(Action.STRONG_SELL).hex == DEFAULT_PALETTE.strong_sell
    assert vm.action_visual(Action.STRONG_BUY).tone == "bullish"
    assert vm.action_visual(Action.STRONG_SELL).tone == "bearish"
    assert vm.action_visual(Action.HOLD).tone == "neutral"


def test_action_visual_survives_enum_identity_mismatch():
    """回歸:Streamlit @st.cache_resource 熱重載後,快取 orchestrator 產出的「舊 class」Action
    與重載後的 emoji/hex 表「新 class」身分不一致 → 以 enum 當 key 會 KeyError。改用 name 對照後穩健。
    """
    import enum

    from multi_agent_system.notifications import emoji_for

    Stale = enum.Enum("Action", {a.name: a.value for a in Action})  # 同名同值、身分不同
    assert Stale.ADD is not Action.ADD                              # 重現 bug 的前提

    av = vm.action_visual(Stale.ADD)          # 修好前：_ACTION_EMOJI[Stale.ADD] → KeyError
    assert av.emoji == "🟢" and av.tone == "bullish" and av.hex == DEFAULT_PALETTE.add
    assert emoji_for(Stale.STRONG_SELL) == "🔴"    # 查表函式本身也穩健


def test_hex_to_rgba():
    assert vm.hex_to_rgba("#22c55e", 0.14) == "rgba(34,197,94,0.14)"
    with pytest.raises(ValueError):
        vm.hex_to_rgba("#fff", 0.1)


def test_score_breakdown_contributions():
    d = _decision(Action.STRONG_BUY, 0.87)
    rows = {r.key: r for r in vm.score_breakdown(d)}
    # 貢獻 = 權重 * 得分
    assert rows["macro"].contribution == pytest.approx(0.30 * 0.8)
    assert rows["technical"].contribution == pytest.approx(0.50 * 0.9)
    assert rows["allocation"].contribution == pytest.approx(0.20 * 0.7)


def test_breakdown_handles_missing_expert():
    verdicts = {
        "macro": AgentVerdict("MacroAgent", True, 0.8, "ok", {}),
        "technical": AgentVerdict.unavailable("TechnicalAgent", "no data"),
        "allocation": AgentVerdict("AllocationAgent", True, 0.7, "ok", {}),
    }
    d = FinalDecision("9999", Action.HOLD, None, True, False, verdicts, "abstain")
    rows = {r.key: r for r in vm.score_breakdown(d)}
    assert rows["technical"].contribution is None
    assert rows["technical"].available is False
    df = vm.breakdown_chart_df(d)
    tech_row = df[df["expert"].str.startswith("技術")].iloc[0]
    assert tech_row["score_label"] == "N/A"
    assert tech_row["score"] == 0.0  # 圖表以 0 呈現


def test_final_score_text():
    assert vm.final_score_text(_decision(Action.ADD, 0.65)) == "0.650"
    abstain = FinalDecision("X", Action.HOLD, None, True, False, {}, "")
    assert vm.final_score_text(abstain) == "N/A"


def test_should_notify_filters_hold_and_abstain():
    assert notify.should_notify(_decision(Action.STRONG_BUY, 0.9)) is True
    assert notify.should_notify(_decision(Action.REDUCE, 0.25)) is True
    assert notify.should_notify(_decision(Action.HOLD, 0.5)) is False
    abstain = FinalDecision("X", Action.HOLD, None, True, False, {}, "")
    assert notify.should_notify(abstain) is False


def test_format_notification_contains_key_fields():
    txt = notify.format_notification(_decision(Action.STRONG_SELL, 0.05, risk=True))
    assert "2330" in txt
    assert "強烈賣出" in txt
    assert "風控減碼" in txt


def test_custom_palette_override():
    from multi_agent_system.ui.theme import Palette

    custom = Palette(strong_buy="#000001")
    assert vm.action_visual(Action.STRONG_BUY, custom).hex == "#000001"
