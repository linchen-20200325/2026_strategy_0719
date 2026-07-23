"""view_model.py — 決策 → 視覺呈現的「純」轉換層（無 streamlit 依賴，可單獨測試）。

把 FinalDecision 轉成 badge/圖表/通知所需的資料結構（顏色、標籤、長條圖 DataFrame）。
Streamlit render 函式（components.py）只負責畫,不做資料整形 → 關注點分離、易測。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import FUSION_WEIGHTS

from ..contracts import Action, FinalDecision
from ..notifications import ACTION_EMOJI as _ACTION_EMOJI
from .theme import DEFAULT_PALETTE, Palette

# 交通號誌 emoji 由核心 notifications 提供;三態 tone 由 Action.tone 提供(皆 SSOT,不重刻)。
_EXPERT_LABELS: dict[str, str] = {
    "macro": "總經 Macro",
    "technical": "技術 Technical",
    "fundamental": "基本面 Fundamental",
    "allocation": "配置 Allocation",
}
_WEIGHT_KEYS = ("macro", "technical", "fundamental", "allocation")


@dataclass(frozen=True)
class ActionVisual:
    emoji: str
    label: str      # 含中英（來自 Action.value）
    hex: str
    tone: str       # bullish / neutral / bearish


@dataclass(frozen=True)
class BreakdownRow:
    key: str
    label: str
    weight: float
    score: float | None
    contribution: float | None   # weight * score（缺料時 None）
    reason: str
    available: bool


def action_visual(action: Action, palette: Palette = DEFAULT_PALETTE) -> ActionVisual:
    """行動 → (emoji, 標籤, 顏色, 傾向)。"""
    hexmap = {
        Action.STRONG_BUY: palette.strong_buy,
        Action.ADD: palette.add,
        Action.HOLD: palette.hold,
        Action.REDUCE: palette.reduce,
        Action.STRONG_SELL: palette.strong_sell,
    }
    return ActionVisual(
        emoji=_ACTION_EMOJI[action],
        label=action.value,
        hex=hexmap[action],
        tone=action.tone,
    )


def hex_to_rgba(hex_str: str, alpha: float) -> str:
    """#RRGGBB → rgba(r,g,b,alpha)，供半透明卡片底色（在明暗主題皆可讀）。"""
    h = hex_str.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"預期 #RRGGBB，收到 {hex_str!r}")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def score_breakdown(decision: FinalDecision) -> list[BreakdownRow]:
    """各專家的得分/權重/貢獻度拆解（供圖表 + 展開原因）。

    權重採**有效（重新歸一化後）權重**：只就「有評分」的專家歸一化，故貢獻總和 = Final Score。
    基本面為選填 → 該標的無財報時不列此列（僅列有給評分或必需的專家）。
    """
    keys = [k for k in _WEIGHT_KEYS if k in decision.verdicts]
    scored = [k for k in keys if decision.verdicts[k].score is not None]
    total_w = sum(FUSION_WEIGHTS[k] for k in scored) or 1.0

    rows: list[BreakdownRow] = []
    for key in keys:
        v = decision.verdicts[key]
        # 選填的基本面若缺席（無評分）→ 不列（保持既有三專家拆解不變）。
        if key == "fundamental" and v.score is None:
            continue
        if v.score is not None:
            weight = FUSION_WEIGHTS[key] / total_w      # 有效權重
            contribution = round(weight * v.score, 4)
        else:
            weight = FUSION_WEIGHTS[key]                # 缺料（必需專家）→ 名目權重
            contribution = None
        rows.append(
            BreakdownRow(
                key=key,
                label=_EXPERT_LABELS[key],
                weight=weight,
                score=v.score,
                contribution=contribution,
                reason=v.reason,
                available=v.available,
            )
        )
    return rows


def breakdown_chart_df(decision: FinalDecision) -> pd.DataFrame:
    """組長條圖用 DataFrame（缺料專家得分以 0 呈現,並在標籤標 N/A）。"""
    rows = score_breakdown(decision)
    return pd.DataFrame(
        [
            {
                "expert": r.label,
                "score": 0.0 if r.score is None else r.score,
                "score_label": "N/A" if r.score is None else f"{r.score:.2f}",
                "weight_label": f"權重 {r.weight:.0%}",
                "available": r.available,
            }
            for r in rows
        ]
    )


def final_score_text(decision: FinalDecision) -> str:
    return "N/A" if decision.final_score is None else f"{decision.final_score:.3f}"
