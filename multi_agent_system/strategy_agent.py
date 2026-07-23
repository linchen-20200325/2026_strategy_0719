"""strategy_agent.py — 策略專家 (Strategy Agent / 團隊大腦)。

單一職責
--------
決策融合 (Decision Fusion)：把三位專家的 [0,1] 得分依預設權重加權為 Final Score，
映射到五大交易行動，並整合各專家診斷原因。

計算式
------
    Final Score = Σ_k w_k * S_k / Σ_k w_k      （k ∈ 可用專家，缺者不計 → 重新歸一化）
    （w = 總經 0.24 / 技術 0.40 / 基本面 0.20 / 配置 0.16，SSOT: config.FUSION_WEIGHTS）

    「基本面」為**選填專家**：macro:technical:allocation 維持原 3:5:2 比例，故某標的無財報
    （ETF/基金）時重新歸一化會**精確還原** 0.30/0.50/0.20 —— 既有行為不變，基本面只在有資料時加權。

    Final Score ∈ [0,1] → 行動（含下界門檻，SSOT: config）：
        >= 0.80 強烈買進 Strong Buy
        >= 0.60 適度加碼 Add
        >= 0.40 持股觀望 Hold
        >= 0.20 適度減碼 Reduce
        <  0.20 強烈賣出 Strong Sell

風控硬約束 (Hard Override)
--------------------------
若配置專家觸發集中度風控 (risk_control_triggered)，最終行動「不得比適度減碼更偏多」：
Strong Buy / Add / Hold 一律下修為 Reduce。此為 MPT 風控凌駕短線訊號的設計。

缺料處理 (Fail Loud, 不臆造)
----------------------------
預設 require_all_experts=True：任一專家無資料 → 不硬湊、不偷偷重新分配權重，
而是 abstain（回 Hold + final_score=None + abstained=True），並在 rationale 說明缺哪一塊。
"""

from __future__ import annotations

import math

from config import (
    ADD_MIN,
    FUSION_WEIGHTS,
    HOLD_MIN,
    REDUCE_MIN,
    STRONG_BUY_MIN,
)

from .contracts import (
    ACTION_BULLISH_ORDER,
    Action,
    AgentVerdict,
    FinalDecision,
)
from .numerics import clamp, weighted_mean

AGENT_NAME = "StrategyAgent"

# 融合順序（對齊 config.FUSION_WEIGHTS 的鍵）。基本面為選填，其餘三者為必需。
_ALL_KEYS = ("macro", "technical", "fundamental", "allocation")
_REQUIRED_KEYS = ("macro", "technical", "allocation")


def _ok(v: AgentVerdict) -> bool:
    return v.available and v.score is not None


class StrategyAgent:
    """決策融合專家。"""

    def __init__(self, require_all_experts: bool = True) -> None:
        self.require_all_experts = require_all_experts

    def decide(
        self,
        tw_stock_id: str,
        macro: AgentVerdict,
        technical: AgentVerdict,
        allocation: AgentVerdict,
        fundamental: AgentVerdict | None = None,
    ) -> FinalDecision:
        # 基本面選填：未提供 → 視為缺席（不 abstain，退回原三專家歸一化）。
        if fundamental is None:
            fundamental = AgentVerdict.unavailable("FundamentalAgent", "未提供基本面評分")
        verdicts = {
            "macro": macro, "technical": technical,
            "fundamental": fundamental, "allocation": allocation,
        }

        # --- 缺料檢查：只有「必需」專家缺才會 abstain；基本面缺不阻斷 ---
        missing_required = [k for k in _REQUIRED_KEYS if not _ok(verdicts[k])]
        available = [k for k in _ALL_KEYS if _ok(verdicts[k])]
        risk_control_triggered = bool(
            allocation.available
            and allocation.diagnostics.get("risk_control_triggered", False)
        )

        # abstain 條件：(a) 要求全員到齊卻有必需缺；(b) 完全無可用專家。
        if (self.require_all_experts and missing_required) or not available:
            return FinalDecision(
                tw_stock_id=tw_stock_id,
                action=Action.HOLD,
                final_score=None,
                abstained=True,
                risk_control_triggered=risk_control_triggered,
                verdicts=verdicts,
                rationale=(
                    f"⚠️ 資料不足，暫停決策 (abstain)：缺少 {missing_required} 專家評分。"
                    "依 Fail-Loud 原則不臆造分數。"
                ),
            )

        # --- 加權融合（partial 模式下對「可用」專家重新歸一化，不讓 None 進入算術）---
        final_score = weighted_mean(
            (FUSION_WEIGHTS[k], verdicts[k].score) for k in available
        )
        final_score = clamp(final_score, 0.0, 1.0)

        action = self._map_action(final_score)

        # --- 風控硬約束 ---
        overridden = False
        if risk_control_triggered and self._more_bullish_than(action, Action.REDUCE):
            action = Action.REDUCE
            overridden = True

        rationale = self._build_rationale(
            final_score, action, verdicts, available, risk_control_triggered, overridden
        )
        return FinalDecision(
            tw_stock_id=tw_stock_id,
            action=action,
            final_score=round(final_score, 4),
            abstained=False,
            risk_control_triggered=risk_control_triggered,
            verdicts=verdicts,
            rationale=rationale,
        )

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _map_action(score: float) -> Action:
        if score >= STRONG_BUY_MIN:
            return Action.STRONG_BUY
        if score >= ADD_MIN:
            return Action.ADD
        if score >= HOLD_MIN:
            return Action.HOLD
        if score >= REDUCE_MIN:
            return Action.REDUCE
        return Action.STRONG_SELL

    @staticmethod
    def _more_bullish_than(a: Action, b: Action) -> bool:
        return ACTION_BULLISH_ORDER.index(a) > ACTION_BULLISH_ORDER.index(b)

    @staticmethod
    def _build_rationale(
        final_score: float,
        action: Action,
        verdicts: dict[str, AgentVerdict],
        available: list[str],
        risk_control: bool,
        overridden: bool,
    ) -> str:
        # partial：有「必需」專家缺席才算（選填的基本面缺席不算 → 保持既有三專家輸出不變）。
        partial = any(k not in available for k in _REQUIRED_KEYS)
        total_w = math.fsum(FUSION_WEIGHTS[k] for k in available)
        head = f"Final Score = {final_score:.3f} → {action.value}"
        if partial:
            head += "（partial：僅就可用專家重新歸一化）"
        lines = [head]
        for key, label in (
            ("macro", "總經"),
            ("technical", "技術"),
            ("fundamental", "基本面"),
            ("allocation", "配置"),
        ):
            v = verdicts[key]
            if key == "fundamental" and key not in available:
                continue   # 選填專家缺席 → 不列（既有三專家卡片輸出完全不變）
            if key in available:
                wt = f"{FUSION_WEIGHTS[key] / total_w:.0%}"   # 有效（重新歸一化後）權重
                score_txt = f"{v.score:.3f}"
            else:
                wt = f"{FUSION_WEIGHTS[key]:.0%}"             # 必需但缺（partial）→ 原始權重 + N/A
                score_txt = "N/A"
            lines.append(f"  ├ {label}(w={wt}, 分={score_txt})：{v.reason}")
        if risk_control:
            note = "，已硬性下修至『適度減碼』" if overridden else ""
            lines.append(f"  └ 🚨 集中度風控生效{note}")
        return "\n".join(lines)
