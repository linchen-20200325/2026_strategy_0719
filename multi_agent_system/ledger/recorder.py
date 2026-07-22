"""recorder.py — 把大盤判讀寫進 ledger（record 階段）。L2。

在 pipeline 推播完後呼叫，將 market_regime 的 (label, overall) 存成一筆 Judgment。
**record 失敗不可擋推播**（§8.1 #5 失敗降級）：loud log + 回 None（fail token），不 raise。
"""

from __future__ import annotations

import logging
from datetime import datetime

from config import (
    REGIME_UNTAGGED,
    REGIME_YIELD_INVERTED,
    REGIME_YIELD_NORMAL,
    YIELD_INVERSION_PCT,
    now_tw,
)

from .store import Judgment, append_judgment

logger = logging.getLogger(__name__)


def regime_of(yield_spread_pct: float | None) -> str:
    """判讀當下 regime（第一軸：殖利率曲線）。spread 缺 → 未標記（不臆造）。"""
    if yield_spread_pct is None:
        return REGIME_UNTAGGED
    return REGIME_YIELD_INVERTED if yield_spread_pct <= YIELD_INVERSION_PCT else REGIME_YIELD_NORMAL


def record_market_regime(
    *,
    label: str,
    overall: float,
    session: str,
    regime: str = REGIME_UNTAGGED,
    when: datetime | None = None,
    path: str | None = None,
) -> Judgment | None:
    """存一筆大盤判讀。成功回 Judgment;失敗 loud log + 回 None（不擋推播）。

    when：判讀當下（預設 now_tw()）。judged_date 取其台灣日期，與對帳進場對齊一致。
    regime：判讀當下市場 regime（用 regime_of() 由殖利率 spread 導出），供分 regime 對帳。
    """
    try:
        stamp = when or now_tw()
        j = Judgment(
            judged_at=stamp.isoformat(),
            judged_date=stamp.date().isoformat(),
            session=session,
            label=label,
            overall=round(float(overall), 6),
            regime=regime,
        )
        append_judgment(j, path=path)
        logger.info(
            "ledger 已記錄判讀：%s %s（綜合偏多度 %.3f）", j.judged_date, label, overall
        )
        return j
    except Exception as exc:  # noqa: BLE001 — record 為次要，失敗須 loud 但絕不擋推播（§8.1#5）
        logger.error("ledger 記錄失敗（不擋推播）：%s", exc)
        return None
