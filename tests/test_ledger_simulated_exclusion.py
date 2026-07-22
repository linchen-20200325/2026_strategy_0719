"""test_ledger_simulated_exclusion.py — F：模擬總經判讀不污染 track record。

fund.db 讀不到 → 用寫死的模擬總經（is_simulated=True）。這種判讀**不可**當實測計入
命中率/淨值（§1 錯值比缺值危險）。本檔驗：is_simulated 落地/向後相容/對帳排除（可見不靜默）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

from config import (
    REGIME_LABEL_BULL,
    REGIME_LABEL_NEUTRAL,
    REGIME_UNTAGGED,
)
from multi_agent_system.contracts import Action
from multi_agent_system.ledger.reconcile import PriceBar
from multi_agent_system.ledger.recorder import record_market_regime
from multi_agent_system.ledger.report import build_report, format_report
from multi_agent_system.ledger.stock_recorder import record_stock_judgments
from multi_agent_system.ledger.stock_store import read_stock_judgments
from multi_agent_system.ledger.store import Judgment, read_judgments

TW = timezone(timedelta(hours=8))


def _seq(start: str, n: int, fn) -> list[PriceBar]:
    d0 = date.fromisoformat(start)
    return [PriceBar(d0 + timedelta(days=i), float(fn(i))) for i in range(n)]


def _J(dstr, session, label, regime=REGIME_UNTAGGED, is_simulated=False):
    return Judgment(f"{dstr}T07:30:00+08:00", dstr, session, label, 0.5, regime, is_simulated)


# ───────────────────────────── 落地 / 向後相容
def test_record_market_regime_persists_is_simulated(tmp_path):
    p = str(tmp_path / "l.jsonl")
    when = datetime(2026, 7, 22, 7, 30, tzinfo=TW)
    record_market_regime(label=REGIME_LABEL_BULL, overall=0.7, session="morning",
                         is_simulated=True, when=when, path=p)
    assert read_judgments(path=p)[0].is_simulated is True


def test_old_row_without_is_simulated_defaults_false(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text(
        '{"judged_at":"x","judged_date":"2026-07-01","session":"morning",'
        '"label":"偏多","overall":0.7,"regime":"正常"}\n',
        encoding="utf-8",
    )
    assert read_judgments(path=str(p))[0].is_simulated is False   # 舊列 → 視為真實


def test_record_stock_judgments_persists_is_simulated(tmp_path):
    p = str(tmp_path / "s.jsonl")
    r = NS(decision=NS(tw_stock_id="2330", action=Action.ADD, final_score=0.6, abstained=False),
           packet=NS(technical=NS(close=100.0, as_of="2026-07-21")))
    record_stock_judgments([r], session="morning", is_simulated=True, path=p)
    assert read_stock_judgments(path=p)[0].is_simulated is True


# ───────────────────────────── ★ 對帳排除模擬判讀（可見、不靜默）
def test_build_report_excludes_simulated_but_counts_it():
    bars = _seq("2026-01-01", 30, lambda i: 100 + i)     # 單調上漲
    js = [
        _J("2026-01-01", "morning", REGIME_LABEL_BULL, is_simulated=False),  # 真實 → 計
        _J("2026-01-02", "morning", REGIME_LABEL_BULL, is_simulated=True),   # 模擬 → 排除
        _J("2026-01-03", "morning", REGIME_LABEL_NEUTRAL, is_simulated=True),  # 模擬 → 排除
    ]
    rep = build_report(js, bars, horizon_n=5, band=0.005)
    assert rep.n_total == 3           # 去重後總數（含模擬）
    assert rep.n_scored == 1          # 只有真實那筆對帳
    assert rep.n_simulated == 2       # 模擬排除、但**可見**（不靜默消失）
    assert rep.buckets[REGIME_LABEL_BULL].n == 1     # 只計真實
    assert rep.n_total == rep.n_scored + rep.n_pending + rep.n_simulated   # 帳目守恆


def test_all_simulated_yields_no_scored():
    bars = _seq("2026-01-01", 30, lambda i: 100 + i)
    js = [_J("2026-01-01", "morning", REGIME_LABEL_BULL, is_simulated=True)]
    rep = build_report(js, bars, horizon_n=5, band=0.005)
    assert rep.n_scored == 0 and rep.n_simulated == 1
    assert rep.directional_hit_rate is None          # 無真實樣本 → 不下結論


def test_format_shows_simulated_excluded():
    bars = _seq("2026-01-01", 30, lambda i: 100 + i)
    js = [
        _J("2026-01-01", "morning", REGIME_LABEL_BULL, is_simulated=False),
        _J("2026-01-02", "morning", REGIME_LABEL_BULL, is_simulated=True),
    ]
    txt = format_report(build_report(js, bars, horizon_n=5, band=0.005))
    assert "模擬排除 1" in txt
