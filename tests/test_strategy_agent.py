"""test_strategy_agent.py — 融合算術、五action映射、風控硬約束、abstain。"""

from __future__ import annotations

import pytest

from multi_agent_system import StrategyAgent
from multi_agent_system.contracts import Action, AgentVerdict


def _v(agent, score, diagnostics=None):
    return AgentVerdict(
        agent=agent, available=True, score=score, reason=f"{agent}={score}",
        diagnostics=diagnostics or {},
    )


def _decide(macro, tech, alloc, alloc_diag=None):
    return StrategyAgent().decide(
        "T", _v("macro", macro), _v("tech", tech),
        _v("alloc", alloc, alloc_diag),
    )


def test_fusion_arithmetic():
    d = _decide(0.8, 0.9, 0.7)
    assert d.final_score == pytest.approx(0.83)
    assert d.action == Action.STRONG_BUY


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.80, Action.STRONG_BUY),
        (0.79, Action.ADD),
        (0.60, Action.ADD),
        (0.59, Action.HOLD),
        (0.40, Action.HOLD),
        (0.39, Action.REDUCE),
        (0.20, Action.REDUCE),
        (0.19, Action.STRONG_SELL),
        (0.0, Action.STRONG_SELL),
    ],
)
def test_action_threshold_mapping(score, expected):
    # 三專家同分 → final == score（權重和=1）
    d = _decide(score, score, score)
    assert d.final_score == pytest.approx(score)
    assert d.action == expected


def test_risk_control_hard_override_downgrades_to_reduce():
    # 分數本應 Strong Buy，但配置風控觸發 → 強制降為 Reduce
    d = _decide(0.9, 0.9, 0.9, alloc_diag={"risk_control_triggered": True})
    assert d.risk_control_triggered is True
    assert d.action == Action.REDUCE
    assert "硬性下修" in d.rationale


def test_risk_control_does_not_upgrade_strong_sell():
    d = _decide(0.05, 0.05, 0.05, alloc_diag={"risk_control_triggered": True})
    assert d.action == Action.STRONG_SELL  # 已比 Reduce 更空，不動


def test_abstain_when_expert_missing():
    d = StrategyAgent().decide(
        "T",
        _v("macro", 0.8),
        AgentVerdict.unavailable("tech", "no data"),
        _v("alloc", 0.7),
    )
    assert d.abstained is True
    assert d.final_score is None
    assert d.action == Action.HOLD
    assert "abstain" in d.rationale.lower() or "資料不足" in d.rationale


def test_partial_allowed_when_not_requiring_all():
    # require_all_experts=False：缺技術面時，就「可用專家」重新歸一化，不讓 None 進入算術。
    agent = StrategyAgent(require_all_experts=False)
    d = agent.decide(
        "T", _v("macro", 0.9),
        AgentVerdict.unavailable("tech", "no data"),
        _v("alloc", 0.9),
    )
    assert not d.abstained
    # (0.3*0.9 + 0.2*0.9) / (0.3+0.2) = 0.9
    assert d.final_score == pytest.approx(0.9)
    assert d.action == Action.STRONG_BUY
    assert "partial" in d.rationale


def test_all_missing_abstains_even_in_partial_mode():
    agent = StrategyAgent(require_all_experts=False)
    d = agent.decide(
        "T",
        AgentVerdict.unavailable("macro", "x"),
        AgentVerdict.unavailable("tech", "x"),
        AgentVerdict.unavailable("alloc", "x"),
    )
    assert d.abstained is True
    assert d.action == Action.HOLD
