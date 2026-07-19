"""test_numerics.py — clamp / linear_map / annualized_sharpe。"""

from __future__ import annotations

import math

import pytest

from multi_agent_system.numerics import annualized_sharpe, clamp, linear_map


def test_clamp_bounds():
    assert clamp(5, 0, 1) == 1
    assert clamp(-5, 0, 1) == 0
    assert clamp(0.3, 0, 1) == 0.3


def test_clamp_bad_interval_raises():
    with pytest.raises(ValueError):
        clamp(0.5, 1.0, 0.0)


def test_linear_map_basic_and_clamp():
    assert linear_map(1.5, 0, 3, 0, 1) == pytest.approx(0.5)
    # 超出來源區間 → clamp 到終點
    assert linear_map(-1, 0, 3, 0, 1) == 0.0
    assert linear_map(10, 0, 3, 0, 1) == 1.0


def test_linear_map_degenerate_interval():
    # x0==x1：退化，回傳 y1（終點）
    assert linear_map(5, 2, 2, 0, 1) == 1.0


def test_linear_map_descending_output():
    # CPI 型：值越大分越低
    assert linear_map(2, 2, 5, 1, 0) == pytest.approx(1.0)
    assert linear_map(5, 2, 5, 1, 0) == pytest.approx(0.0)
    assert linear_map(3.5, 2, 5, 1, 0) == pytest.approx(0.5)


def test_annualized_sharpe_formula():
    returns = [0.001, 0.002, -0.001, 0.0015, 0.0005]
    got = annualized_sharpe(returns, rf_annual=0.0, periods_per_year=252)
    import numpy as np

    arr = np.asarray(returns)
    expected = arr.mean() / arr.std(ddof=1) * math.sqrt(252)
    assert got == pytest.approx(expected)


def test_annualized_sharpe_zero_vol_raises():
    with pytest.raises(ZeroDivisionError):
        annualized_sharpe([0.01, 0.01, 0.01])


def test_annualized_sharpe_too_few_raises():
    with pytest.raises(ValueError):
        annualized_sharpe([0.01])


def test_annualized_sharpe_nan_raises():
    with pytest.raises(ValueError):
        annualized_sharpe([0.01, float("nan"), 0.02])
