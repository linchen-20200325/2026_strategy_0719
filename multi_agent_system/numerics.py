"""numerics.py — 純數值工具（無 I/O、無狀態），供各專家共用，避免邏輯重複 (DRY)。

集中放置：clamp、容差比較、Sharpe Ratio 計算。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import numpy as np

from config import (
    FLOAT_ABS_TOL,
    FLOAT_REL_TOL,
    RF_ANNUAL_RATE,
    TRADING_DAYS_PER_YEAR,
)


def clamp(value: float, low: float, high: float) -> float:
    """將 value 夾在 [low, high]。防止子分數溢出 [0,1]。"""
    if low > high:
        raise ValueError(f"clamp 區間非法：low={low} > high={high}")
    return max(low, min(high, value))


def isclose(a: float, b: float) -> bool:
    """浮點相等一律走容差（對照 CLAUDE.md §4.3：禁止 ==）。"""
    return math.isclose(a, b, rel_tol=FLOAT_REL_TOL, abs_tol=FLOAT_ABS_TOL)


def linear_map(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """把 x 從 [x0, x1] 線性映射到 [y0, y1]，並 clamp 在 [min(y0,y1), max(y0,y1)]。

    用於「指標值 → 0..1 得分」的通用轉換。x0==x1 時退化為階梯（回傳 y1）。
    """
    if isclose(x0, x1):
        # 區間退化：無法內插，回傳終點值（呼叫端須確保語意正確）。
        return y1
    t = (x - x0) / (x1 - x0)
    y = y0 + t * (y1 - y0)
    return clamp(y, min(y0, y1), max(y0, y1))


def weighted_mean(pairs: Iterable[tuple[float, float | None]]) -> float | None:
    """加權平均：忽略 None 子分量、以「在場」權重重新歸一化；全缺 → None（不捏 0）。

    pairs：(weight, value_or_None) 的可迭代。各專家「缺子分量則重新歸一化」的共用原語
    （避免同一算式在 technical / fundamental / strategy / macro 各自重刻而靜默漂移）。
    """
    present = [(w, v) for w, v in pairs if v is not None]
    if not present:
        return None
    total_w = math.fsum(w for w, _ in present)
    if total_w <= 0:
        return None
    return math.fsum(w * v for w, v in present) / total_w


def annualized_sharpe(
    returns: Sequence[float],
    *,
    rf_annual: float = RF_ANNUAL_RATE,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """年化夏普比率 (Sharpe Ratio)。

    金融原理
    --------
    夏普比率衡量「每承擔一單位總風險（波動）所獲得的超額報酬」，
    是 MPT 下比較資產風險調整後績效的核心指標。

    數學公式
    --------
        Sharpe_annual = (E[R_p] - R_f) / σ_p * sqrt(P)

    其中
        R_p           : 期間（日）報酬序列
        E[R_p]        : 期間報酬平均值 mean(returns)
        R_f           : 每期無風險利率 = rf_annual / periods_per_year
        σ_p           : 期間報酬「樣本」標準差 std(returns, ddof=1)
        P             : 年化因子 periods_per_year（台/美股日頻 = 252）

    邊界防禦
    --------
    * 少於 2 筆 → 無法估計樣本標準差 → raise（Fail Loud，不回傳假 0）。
    * σ_p == 0（資產完全無波動）→ Sharpe 數學上發散，raise 而非回傳 inf/0。
    """
    arr = np.asarray(returns, dtype="float64")
    if arr.ndim != 1:
        raise ValueError("returns 必須為一維序列")
    if arr.size < 2:
        raise ValueError(f"Sharpe 需要 >= 2 筆報酬，收到 {arr.size} 筆")
    if not np.all(np.isfinite(arr)):
        raise ValueError("returns 含 NaN / Inf，拒絕計算（Fail Loud）")

    rf_per_period = rf_annual / periods_per_year
    excess = arr - rf_per_period
    std = float(np.std(arr, ddof=1))
    if isclose(std, 0.0):
        # 零波動：分母為 0，Sharpe 無定義。上游應改用其他指標，不可靜默回 0。
        raise ZeroDivisionError("報酬序列標準差為 0（無波動），Sharpe 無定義")

    mean_excess = float(np.mean(excess))
    return mean_excess / std * math.sqrt(periods_per_year)
