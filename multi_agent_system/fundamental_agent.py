"""fundamental_agent.py — 基本面專家 (Fundamental Agent)。

單一職責
--------
把個股「財報品質（毛利率 / 淨利率）+ 月營收動能（YoY）」評成 [0,1] 基本面分數，
1 = 基本面最佳。供策略融合當第 4 位專家（權重 SSOT: config.FUSION_WEIGHTS['fundamental']）。

計算式
------
    S_fund = Σ_k w_k · f_k(x_k) / Σ_k w_k      （k ∈ 有資料的分量，缺者不計 → 重新歸一化）
    f(x)   = linear_map(x, LOW, HIGH, 0, 1)     （x<=LOW→0；x>=HIGH→1；中間線性）

    分量與區間（SSOT: config）：
        毛利率   gross_margin_pct  ∈ [GROSS_MARGIN_LOW, HIGH]
        淨利率   net_margin_pct    ∈ [NET_MARGIN_LOW, HIGH]
        月營收YoY revenue_yoy_pct  ∈ [REVENUE_YOY_LOW, HIGH]

缺料處理（Fail Loud, 不臆造）
----------------------------
* 查無財報（ETF/基金/新股）→ available=False（策略層視為「選填專家缺席」，
  不 abstain，改以其餘專家重新歸一化）。
* 月營收缺（未設 FINMIND_TOKEN → 未落地）→ 該分量不計，只用毛利+淨利。
* 全分量皆缺（財報三欄都 NaN）→ available=False。
"""

from __future__ import annotations

from config import (
    FUNDAMENTAL_SUBWEIGHTS,
    GROSS_MARGIN_HIGH_PCT,
    GROSS_MARGIN_LOW_PCT,
    NET_MARGIN_HIGH_PCT,
    NET_MARGIN_LOW_PCT,
    REVENUE_YOY_HIGH_PCT,
    REVENUE_YOY_LOW_PCT,
)

from .contracts import AgentVerdict, FinancialsSnapshot
from .numerics import linear_map, weighted_mean

AGENT_NAME = "FundamentalAgent"


class FundamentalAgent:
    """財報品質 + 月營收動能 → [0,1] 基本面分數。"""

    def evaluate(
        self,
        financials: FinancialsSnapshot | None,
        revenue_yoy_pct: float | None = None,
    ) -> AgentVerdict:
        if financials is None:
            return AgentVerdict.unavailable(AGENT_NAME, "查無財報（stock_fundamentals）")

        # 各分量：有值才算（缺 → 不計，稍後重新歸一化）。
        comps: dict[str, float] = {}
        if financials.gross_margin_pct is not None:
            comps["gross_margin"] = linear_map(
                financials.gross_margin_pct, GROSS_MARGIN_LOW_PCT, GROSS_MARGIN_HIGH_PCT, 0.0, 1.0
            )
        if financials.net_margin_pct is not None:
            comps["net_margin"] = linear_map(
                financials.net_margin_pct, NET_MARGIN_LOW_PCT, NET_MARGIN_HIGH_PCT, 0.0, 1.0
            )
        if revenue_yoy_pct is not None:
            comps["revenue_yoy"] = linear_map(
                revenue_yoy_pct, REVENUE_YOY_LOW_PCT, REVENUE_YOY_HIGH_PCT, 0.0, 1.0
            )

        if not comps:
            return AgentVerdict.unavailable(
                AGENT_NAME, f"{financials.stock_id} 財報三欄皆缺，無法評基本面"
            )

        score = weighted_mean((FUNDAMENTAL_SUBWEIGHTS[k], comps[k]) for k in comps)

        bits: list[str] = []
        if financials.gross_margin_pct is not None:
            bits.append(f"毛利率{financials.gross_margin_pct:.1f}%")
        if financials.net_margin_pct is not None:
            bits.append(f"淨利率{financials.net_margin_pct:.1f}%")
        if revenue_yoy_pct is not None:
            bits.append(f"月營收YoY{revenue_yoy_pct:+.1f}%")
        partial = "（月營收未落地，僅財報）" if "revenue_yoy" not in comps else ""
        reason = f"{financials.period_label}季報：{' · '.join(bits)}{partial}"

        return AgentVerdict(
            agent=AGENT_NAME,
            available=True,
            score=score,
            reason=reason,
            diagnostics={"components": comps, "period": financials.period_label},
        )
