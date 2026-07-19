"""allocation_agent.py — 資產配置專家 (Asset Allocation Agent)。

單一職責
--------
基於現代投資組合理論 (MPT / Markowitz)，以夏普比率衡量標的的風險調整後吸引力，
並對「單一持股集中度」做強制風控：一旦權重超過上限，強制發出「風控減碼信號」
（壓低配置得分，進而拖低 Final Score）。

金融原理與計算式
----------------
1) 夏普比率 (Sharpe Ratio) — MPT 的風險調整後績效核心指標
         Sharpe = (E[R_p] - R_f) / σ_p            （年化見 numerics.annualized_sharpe）
   映射為 [0,1] 得分：
         sharpe_score = clamp( (Sharpe - FLOOR) / (CAP - FLOOR), 0, 1 )
   （FLOOR=0：無超額報酬 → 0 分；CAP=2：優異 → 1 分）

2) 集中度風控 (Concentration Risk)
   * 原理：MPT 強調分散以降低非系統性風險。單一持股過度集中會放大特異性風險。
   * 判定：breach = current_weight_ratio > max_weight_ratio
   * 超限時強制壓分（overshoot 越大壓越低）：
         overshoot = (current_weight - max_weight) / max_weight
         alloc_score = RISK_CONTROL_SCORE_CAP * (1 - clamp(overshoot, 0, 1))
     使 alloc_score <= RISK_CONTROL_SCORE_CAP(0.25)，並回傳
     risk_control_triggered=True，供策略專家做「不得比減碼更偏多」的硬性約束。

邊界防禦
--------
* 權重不在 [0,1]、上限 <=0 → raise（Fail Loud，配置輸入本應乾淨）。
* 同時給 sharpe 與 returns → 以現成 sharpe 為準並告警；兩者皆無 → unavailable。
* returns 無波動（σ=0）→ numerics 內 raise，於此轉為 unavailable 並附原因。
"""

from __future__ import annotations

from config import (
    DEFAULT_MAX_WEIGHT_RATIO,
    RISK_CONTROL_SCORE_CAP,
    SHARPE_CAP,
    SHARPE_FLOOR,
)

from .contracts import AgentVerdict, PortfolioState
from .numerics import annualized_sharpe, clamp, linear_map

AGENT_NAME = "AllocationAgent"


class AssetAllocationAgent:
    """資產配置 / MPT 風控專家。"""

    def evaluate(self, state: PortfolioState | None) -> AgentVerdict:
        if state is None:
            return AgentVerdict.unavailable(AGENT_NAME, "無投組狀態輸入")

        self._validate_weights(state)

        # --- 取得 Sharpe（現成優先，否則自報酬序列計算）---
        sharpe, sharpe_source, warn = self._resolve_sharpe(state)
        if sharpe is None:
            return AgentVerdict.unavailable(AGENT_NAME, warn or "無法取得 Sharpe")

        sharpe_score = linear_map(sharpe, SHARPE_FLOOR, SHARPE_CAP, 0.0, 1.0)

        # --- 集中度風控 ---
        breach = state.current_weight_ratio > state.max_weight_ratio
        if breach:
            overshoot = (
                state.current_weight_ratio - state.max_weight_ratio
            ) / state.max_weight_ratio
            severity = clamp(overshoot, 0.0, 1.0)
            alloc_score = RISK_CONTROL_SCORE_CAP * (1.0 - severity)
        else:
            severity = 0.0
            alloc_score = sharpe_score

        alloc_score = clamp(alloc_score, 0.0, 1.0)

        diagnostics = {
            "sharpe": round(sharpe, 4),
            "sharpe_source": sharpe_source,
            "sharpe_score": round(sharpe_score, 4),
            "current_weight_ratio": state.current_weight_ratio,
            "max_weight_ratio": state.max_weight_ratio,
            "risk_control_triggered": breach,
            "overshoot_severity": round(severity, 4),
        }
        if warn:
            diagnostics["warning"] = warn

        return AgentVerdict(
            agent=AGENT_NAME,
            available=True,
            score=round(alloc_score, 4),
            reason=self._build_reason(sharpe, breach, state, severity),
            diagnostics=diagnostics,
        )

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _validate_weights(state: PortfolioState) -> None:
        w = state.current_weight_ratio
        cap = state.max_weight_ratio
        if not (0.0 <= w <= 1.0):
            raise ValueError(f"current_weight_ratio 必須 ∈ [0,1]，收到 {w}")
        if not (0.0 < cap <= 1.0):
            raise ValueError(f"max_weight_ratio 必須 ∈ (0,1]，收到 {cap}")

    @staticmethod
    def _resolve_sharpe(
        state: PortfolioState,
    ) -> tuple[float | None, str, str | None]:
        """回傳 (sharpe, 來源標記, 告警訊息)。"""
        if state.sharpe is not None:
            warn = None
            if state.returns is not None:
                warn = "同時提供 sharpe 與 returns，採用現成 sharpe"
            return float(state.sharpe), "provided", warn
        if state.returns is not None:
            try:
                s = annualized_sharpe(state.returns)
                return s, "computed_from_returns", None
            except (ValueError, ZeroDivisionError) as exc:
                return None, "computed_from_returns", f"Sharpe 計算失敗：{exc}"
        return None, "none", "未提供 sharpe 亦無 returns"

    @staticmethod
    def _build_reason(
        sharpe: float, breach: bool, state: PortfolioState, severity: float
    ) -> str:
        base = f"Sharpe={sharpe:.2f}"
        if breach:
            return (
                f"🚨 集中度風控觸發：權重 {state.current_weight_ratio:.1%} > 上限 "
                f"{state.max_weight_ratio:.1%}（超限嚴重度 {severity:.0%}），強制減碼。{base}"
            )
        return (
            f"權重 {state.current_weight_ratio:.1%} 於上限 {state.max_weight_ratio:.1%} 內，"
            f"風險調整後吸引力 {base}"
        )


def default_portfolio_state(
    current_weight_ratio: float,
    *,
    sharpe: float | None = None,
    returns: tuple[float, ...] | None = None,
    max_weight_ratio: float = DEFAULT_MAX_WEIGHT_RATIO,
) -> PortfolioState:
    """便捷建構子：套用預設權重上限 config.DEFAULT_MAX_WEIGHT_RATIO。"""
    return PortfolioState(
        current_weight_ratio=current_weight_ratio,
        max_weight_ratio=max_weight_ratio,
        sharpe=sharpe,
        returns=returns,
    )
