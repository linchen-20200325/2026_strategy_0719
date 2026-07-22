"""test_technical_agent_d3.py — D3「順勢 + 回檔進場」融合的交互項行為。

D3 化解「買便宜(均值回歸)」vs「買強勢(順勢)」互斥：**entry = trend × timing**。
回檔(timing 高)只在上升趨勢(trend→1)中加分；下跌趨勢(trend→0)的超賣不接刀（交互項歸零）。

本檔鎖三件事：
  1) 交互項數值正確（uptrend+回檔 = 最高，鎖公式）。
  2) 順勢時回檔 > 過熱；但兩者都仍「可買」（trend 撐盤）。
  3) 逆勢時「便宜」不救分 — 下跌超賣 ≈ 下跌過熱（交互項 0 → 只剩 momentum）。
"""

from __future__ import annotations

import pytest

from multi_agent_system import TechnicalAnalysisAgent, TechnicalSnapshot


def _snap(*, uptrend: bool, cheap: bool) -> TechnicalSnapshot:
    """close=100 固定；用 MA 排列編碼趨勢、用 %B+RSI 編碼回檔/過熱、動能中性(kd=d、籌碼0)。"""
    ma20, ma60 = (95.0, 90.0) if uptrend else (105.0, 110.0)   # 上升: close>MA20>MA60 → ma_align=1
    if cheap:                                                  # 空頭: close<MA20<MA60 → ma_align=0
        upper, lower, rsi = 120.0, 98.0, 32.0    # %B≈0.09 便宜 + RSI 偏低（回檔/超賣）
    else:
        upper, lower, rsi = 102.0, 80.0, 68.0    # %B≈0.91 昂貴 + RSI 偏高（過熱）
    return TechnicalSnapshot(
        stock_id="2330", as_of="2026-07-18", close=100.0,
        rsi=rsi, upper_band=upper, lower_band=lower,
        ma20=ma20, ma60=ma60, kd_k=50.0, kd_d=50.0, total_net_lots=0.0,   # momentum 中性
    )


def _score(*, uptrend: bool, cheap: bool) -> float:
    return TechnicalAnalysisAgent().evaluate(_snap(uptrend=uptrend, cheap=cheap)).score


# ───────────────────────────── 1) 交互項數值（鎖公式）
def test_uptrend_pullback_is_best_and_exact():
    # trend=1、timing≈0.9295、momentum≈0.3929 →
    # 0.45·1 + 0.35·(1·0.9295) + 0.20·0.3929 = 0.8539（組間權重 0.45/0.35/0.20）。
    v = TechnicalAnalysisAgent().evaluate(_snap(uptrend=True, cheap=True))
    assert v.score == pytest.approx(0.8539, abs=1e-3)
    assert v.diagnostics["grp_trend"] == pytest.approx(1.0)
    assert v.diagnostics["subcomponents_used"] == 5


# ───────────────────────────── 2) 順勢：回檔 > 過熱，但都可買
def test_in_uptrend_pullback_beats_overbought():
    assert _score(uptrend=True, cheap=True) > _score(uptrend=True, cheap=False)


def test_uptrend_overbought_stays_moderate_not_floor():
    # 順勢過熱 → trend 撐盤，仍中性偏上（非地板），但明顯低於順勢回檔。
    s = _score(uptrend=True, cheap=False)
    assert 0.4 < s < 0.65


# ───────────────────────────── 3) ★逆勢：便宜不救分（不接下跌的刀）
def test_downtrend_oversold_stays_low():
    # 「超賣便宜」但空頭排列 → entry = trend×timing = 0 → 低分（<0.2）。
    assert _score(uptrend=False, cheap=True) < 0.2


def test_pullback_only_helps_in_uptrend():
    # 同樣「便宜/回檔」，順勢遠高於逆勢 —— 交互項的核心。
    assert _score(uptrend=True, cheap=True) > _score(uptrend=False, cheap=True) + 0.5


def test_in_downtrend_cheap_and_expensive_are_near_equal():
    # 逆勢時 timing 被交互項歸零 → 便宜 ≈ 昂貴（都只剩 momentum 貢獻）。
    cheap = _score(uptrend=False, cheap=True)
    exp = _score(uptrend=False, cheap=False)
    assert cheap == pytest.approx(exp, abs=1e-9)


# ───────────────────────────── 診斷欄位：無趨勢 → 不捏 trend（向後相容路徑）
def test_no_trend_group_when_ma_absent():
    # 舊 stock.db（無 MA）→ trend 群缺席，退回 timing 均值回歸；不出現 grp_trend。
    snap = TechnicalSnapshot(
        stock_id="X", as_of="2026-07-18", close=100.0,
        rsi=30.0, upper_band=120.0, lower_band=80.0,   # 只有 %B + RSI
    )
    v = TechnicalAnalysisAgent().evaluate(snap)
    assert "grp_trend" not in v.diagnostics
    assert "grp_timing" in v.diagnostics
    assert "grp_momentum" not in v.diagnostics
    assert v.score == pytest.approx(0.75)   # 向後相容：%B(0.5 cheap)+RSI(1.0) 均值 = 0.75
