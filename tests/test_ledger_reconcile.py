"""test_ledger_reconcile.py — 判讀對帳純函式（forward-test 核心）。

覆蓋:進出場對齊(盤前/收盤後)、前瞻報酬、命中判定(偏多/偏空/中性)、
邊界(未到 T+N / 無進場 / 週末跳空 / ÷0 / 升冪防呆)，
以及 SSOT 一致性(reconcile 分類的 label == market_regime 產出的 label)。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from config import (
    LEDGER_HIT_BAND_RATIO,
    REGIME_LABEL_BEAR,
    REGIME_LABEL_BULL,
    REGIME_LABEL_NEUTRAL,
)
from multi_agent_system.ledger.reconcile import (
    STATUS_PENDING,
    STATUS_SCORED,
    PriceBar,
    classify_hit,
    forward_return,
    reconcile,
)

BAND = LEDGER_HIT_BAND_RATIO  # 0.005


def _bars(*pairs: tuple[str, float]) -> list[PriceBar]:
    return [PriceBar(date.fromisoformat(d), o) for d, o in pairs]


def _seq(start_iso: str, n: int, price_fn) -> list[PriceBar]:
    """n 個連續(交易)日 open 序列;每列即一交易日（reconcile 視角）。"""
    d0 = date.fromisoformat(start_iso)
    return [PriceBar(d0 + timedelta(days=i), float(price_fn(i))) for i in range(n)]


# ------------------------------------------------------------------ forward_return
def test_forward_return_basic():
    assert forward_return(100.0, 103.0) == pytest.approx(0.03)
    assert forward_return(100.0, 97.0) == pytest.approx(-0.03)


def test_forward_return_zero_or_negative_entry_raises():
    with pytest.raises(ValueError):
        forward_return(0.0, 100.0)          # ÷0 → 炸掉，不 silent（§4.4）
    with pytest.raises(ValueError):
        forward_return(-5.0, 100.0)


# ------------------------------------------------------------------ classify_hit
def test_classify_hit_bull():
    assert classify_hit(REGIME_LABEL_BULL, 0.02, BAND) is True     # 漲 > band
    assert classify_hit(REGIME_LABEL_BULL, 0.001, BAND) is False   # 漲不到 band
    assert classify_hit(REGIME_LABEL_BULL, -0.02, BAND) is False


def test_classify_hit_bear():
    assert classify_hit(REGIME_LABEL_BEAR, -0.02, BAND) is True    # 跌 > band
    assert classify_hit(REGIME_LABEL_BEAR, -0.001, BAND) is False
    assert classify_hit(REGIME_LABEL_BEAR, 0.02, BAND) is False


def test_classify_hit_neutral():
    assert classify_hit(REGIME_LABEL_NEUTRAL, 0.001, BAND) is True   # 幾乎沒動
    assert classify_hit(REGIME_LABEL_NEUTRAL, 0.02, BAND) is False   # 動太多 → 判錯


def test_classify_hit_unknown_label_and_negative_band_raise():
    with pytest.raises(ValueError):
        classify_hit("看多", 0.02, BAND)             # 未知 label → 炸
    with pytest.raises(ValueError):
        classify_hit(REGIME_LABEL_BULL, 0.02, -0.01)  # 負 band → 炸


# ------------------------------------------------------------------ reconcile: 對齊
def test_reconcile_morning_enters_same_day():
    # 盤前判讀 → 進場 = 判讀日當天 open。
    bars = _bars(("2026-07-13", 100.0), ("2026-07-14", 101.0), ("2026-07-15", 106.0))
    out = reconcile(label=REGIME_LABEL_BULL, judged_date=date(2026, 7, 13),
                    session="morning", bars=bars, horizon_n=2, band=BAND)
    assert out.status == STATUS_SCORED
    assert out.entry_date == date(2026, 7, 13) and out.entry_open == 100.0
    assert out.exit_date == date(2026, 7, 15) and out.exit_open == 106.0
    assert out.forward_return == pytest.approx(0.06)
    assert out.hit is True            # 判偏多、確實漲 6% → 命中


def test_reconcile_afternoon_enters_next_trading_day_over_weekend():
    # 錯誤易發點①:週五收盤後判讀 → 進場 = 下週一 open（週末不在 bars，自動跳過）。
    bars = _bars(
        ("2026-07-17", 100.0),   # Fri（判讀日，收盤後）
        ("2026-07-20", 102.0),   # Mon（進場）
        ("2026-07-21", 101.0),
        ("2026-07-22", 99.0),    # 出場（T+2 交易日）
    )
    out = reconcile(label=REGIME_LABEL_BEAR, judged_date=date(2026, 7, 17),
                    session="afternoon", bars=bars, horizon_n=2, band=BAND)
    assert out.entry_date == date(2026, 7, 20) and out.entry_open == 102.0
    assert out.exit_date == date(2026, 7, 22)
    assert out.forward_return == pytest.approx(99.0 / 102.0 - 1)   # ≈ -2.9%
    assert out.hit is True            # 判偏空、確實跌 → 命中


def test_reconcile_horizon_counts_trading_rows_not_calendar_days():
    # N 交易日 = 往後數 N 列（不是 N 日曆日）。
    bars = _seq("2026-01-01", 30, lambda i: 100 + i)   # open = 100,101,...
    out = reconcile(label=REGIME_LABEL_BULL, judged_date=date(2026, 1, 1),
                    session="morning", bars=bars, horizon_n=20, band=BAND)
    assert out.entry_open == 100.0
    assert out.exit_open == 120.0      # 第 20 列
    assert out.forward_return == pytest.approx(20 / 100)


# ------------------------------------------------------------------ reconcile: 邊界
def test_reconcile_pending_when_horizon_not_reached():
    # 錯誤易發點②:資料尾端、還沒到 T+N → pending（不 crash、不臆造）。
    bars = _seq("2026-01-01", 5, lambda i: 100 + i)
    out = reconcile(label=REGIME_LABEL_BULL, judged_date=date(2026, 1, 1),
                    session="morning", bars=bars, horizon_n=20, band=BAND)
    assert out.status == STATUS_PENDING
    assert out.entry_open == 100.0 and out.exit_open is None
    assert out.hit is None and "未到" in out.reason


def test_reconcile_pending_when_no_entry_bar():
    # 判讀日在所有 bar 之後 → 尚無進場 open → pending。
    bars = _bars(("2026-07-13", 100.0), ("2026-07-14", 101.0))
    out = reconcile(label=REGIME_LABEL_BULL, judged_date=date(2026, 7, 20),
                    session="morning", bars=bars, horizon_n=2, band=BAND)
    assert out.status == STATUS_PENDING and out.entry_open is None


def test_reconcile_zero_entry_open_raises():
    # 錯誤易發點③:進場 open = 0（壞資料）→ 炸掉，不 silent ÷0。
    bars = _bars(("2026-07-13", 0.0), ("2026-07-14", 101.0), ("2026-07-15", 106.0))
    with pytest.raises(ValueError):
        reconcile(label=REGIME_LABEL_BULL, judged_date=date(2026, 7, 13),
                  session="morning", bars=bars, horizon_n=2, band=BAND)


def test_reconcile_unsorted_bars_raise():
    bars = _bars(("2026-07-15", 106.0), ("2026-07-13", 100.0))  # 降冪
    with pytest.raises(ValueError):
        reconcile(label=REGIME_LABEL_BULL, judged_date=date(2026, 7, 13),
                  session="morning", bars=bars, horizon_n=1, band=BAND)


def test_reconcile_bad_horizon_raises():
    bars = _seq("2026-01-01", 5, lambda i: 100 + i)
    with pytest.raises(ValueError):
        reconcile(label=REGIME_LABEL_BULL, judged_date=date(2026, 1, 1),
                  session="morning", bars=bars, horizon_n=0, band=BAND)


# ------------------------------------------------------------------ SSOT 一致性
def test_reconcile_labels_match_market_regime_output():
    # 對帳分類用的 label 必須 == market_regime 實際產出（否則 classify_hit 會炸「未知 label」）。
    from multi_agent_system.market_digest import _regime_word

    assert _regime_word(0.95) == REGIME_LABEL_BULL
    assert _regime_word(0.05) == REGIME_LABEL_BEAR
    assert _regime_word(0.50) == REGIME_LABEL_NEUTRAL
    # 三個 label 皆為 classify_hit 認得的（不會 raise）。
    for lbl in (REGIME_LABEL_BULL, REGIME_LABEL_BEAR, REGIME_LABEL_NEUTRAL):
        classify_hit(lbl, 0.0, BAND)
