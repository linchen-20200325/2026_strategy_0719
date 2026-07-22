"""test_ledger_equity_regime.py — 機械式跟單淨值 + regime 標籤（ledger 延伸）。

覆蓋:regime 導出/持久化/舊列相容、分 regime 命中率、跟單淨值（上漲跟、下跌空手、
換手成本、最後一筆 pending、空集）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from config import (
    REGIME_LABEL_BEAR,
    REGIME_LABEL_BULL,
    REGIME_LABEL_NEUTRAL,
    REGIME_UNTAGGED,
    REGIME_YIELD_INVERTED,
    REGIME_YIELD_NORMAL,
)
from multi_agent_system.ledger.reconcile import PriceBar
from multi_agent_system.ledger.recorder import record_market_regime, regime_of
from multi_agent_system.ledger.report import build_equity, build_report, format_equity
from multi_agent_system.ledger.store import Judgment, read_judgments

TW = timezone(timedelta(hours=8))


def _seq(start: str, n: int, fn) -> list[PriceBar]:
    d0 = date.fromisoformat(start)
    return [PriceBar(d0 + timedelta(days=i), float(fn(i))) for i in range(n)]


def _J(dstr, session, label, regime=REGIME_UNTAGGED):
    return Judgment(f"{dstr}T07:30:00+08:00", dstr, session, label, 0.5, regime)


# ------------------------------------------------------------------ regime 導出 / 持久化
def test_regime_of_yield_curve():
    assert regime_of(-0.1) == REGIME_YIELD_INVERTED
    assert regime_of(0.0) == REGIME_YIELD_INVERTED     # <= 0 視為倒掛
    assert regime_of(0.8) == REGIME_YIELD_NORMAL
    assert regime_of(None) == REGIME_UNTAGGED          # 缺 → 不臆造


def test_regime_persisted_roundtrip(tmp_path):
    p = str(tmp_path / "l.jsonl")
    when = datetime(2026, 7, 22, 7, 30, tzinfo=TW)
    record_market_regime(label=REGIME_LABEL_BULL, overall=0.7, session="morning",
                         regime=REGIME_YIELD_INVERTED, when=when, path=p)
    assert read_judgments(path=p)[0].regime == REGIME_YIELD_INVERTED


def test_old_row_without_regime_defaults_untagged(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text(
        '{"judged_at":"x","judged_date":"2026-07-01","session":"morning","label":"偏多","overall":0.7}\n',
        encoding="utf-8",
    )
    assert read_judgments(path=str(p))[0].regime == REGIME_UNTAGGED   # 向後相容


# ------------------------------------------------------------------ 分 regime 命中率
def test_build_report_by_regime_split():
    bars = _seq("2026-01-01", 30, lambda i: 100 + i)   # 單調上漲
    js = [
        _J("2026-01-01", "morning", REGIME_LABEL_BULL, REGIME_YIELD_NORMAL),    # 漲 → 命中
        _J("2026-01-02", "morning", REGIME_LABEL_BEAR, REGIME_YIELD_INVERTED),  # 漲 → 偏空未命中
    ]
    rep = build_report(js, bars, horizon_n=5, band=0.005)
    assert rep.by_regime[REGIME_YIELD_NORMAL] == (1, 1, 1.0)
    assert rep.by_regime[REGIME_YIELD_INVERTED] == (1, 0, 0.0)


# ------------------------------------------------------------------ 機械式跟單淨值
def test_equity_bull_holds_uptrend_but_pays_entry_cost():
    bars = _seq("2026-01-01", 10, lambda i: 100 * (1.01 ** i))   # 每段 +1%
    js = [_J("2026-01-01", "morning", REGIME_LABEL_BULL),
          _J("2026-01-02", "morning", REGIME_LABEL_BULL),
          _J("2026-01-03", "morning", REGIME_LABEL_BULL)]
    eq = build_equity(js, bars)
    assert eq.n_segments == 2
    assert eq.strategy_return > 0 and eq.market_return > 0
    assert eq.strategy_return < eq.market_return   # 進場換手成本 → 略低於大盤
    assert eq.n_switches == 1                       # 0→1 進場一次


def test_equity_neutral_sits_out_downtrend_beats_market():
    # 下跌盤 + 全程中性（空手）→ 跟單 0 報酬、大盤虧 → 超額為正。這正是「訊號有用」的樣子。
    bars = _seq("2026-01-01", 10, lambda i: 100 * (0.99 ** i))
    js = [_J("2026-01-01", "morning", REGIME_LABEL_NEUTRAL),
          _J("2026-01-02", "morning", REGIME_LABEL_NEUTRAL),
          _J("2026-01-03", "morning", REGIME_LABEL_NEUTRAL)]
    eq = build_equity(js, bars)
    assert abs(eq.strategy_return) < 1e-9      # 空手全程 → 0（exp 一直 0，無換手成本）
    assert eq.market_return < 0
    assert eq.excess > 0


def test_equity_switch_cost_drags_when_price_flat():
    bars = _seq("2026-01-01", 10, lambda i: 100.0)   # 價格不動
    js = [_J("2026-01-01", "morning", REGIME_LABEL_BULL),      # 進場（0→1）扣一次成本
          _J("2026-01-02", "morning", REGIME_LABEL_NEUTRAL)]
    eq = build_equity(js, bars)
    assert eq.n_segments == 1 and eq.n_switches == 1
    assert eq.strategy_return < 0              # 價格不動、只被換手成本拖累


def test_equity_pending_last_and_empty():
    bars = _seq("2026-01-01", 10, lambda i: 100 + i)
    assert build_equity([_J("2026-01-01", "morning", REGIME_LABEL_BULL)], bars).n_segments == 0
    assert build_equity([], bars).n_segments == 0
    assert format_equity(build_equity([], bars)).startswith("📈")


def test_format_equity_shows_excess():
    bars = _seq("2026-01-01", 10, lambda i: 100 * (1.01 ** i))
    js = [_J("2026-01-01", "morning", REGIME_LABEL_BULL),
          _J("2026-01-02", "morning", REGIME_LABEL_BULL)]
    txt = format_equity(build_equity(js, bars))
    assert "機械式跟單" in txt and "超額" in txt and "大盤" in txt
