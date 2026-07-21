"""test_fundamental_agent.py — 基本面專家 + 月營收 YoY 讀取 + 融合（選填專家）。"""

from __future__ import annotations

import sqlite3

import pytest

from multi_agent_system.contracts import AgentVerdict, FinancialsSnapshot
from multi_agent_system.data_agent import DataAggregationAgent
from multi_agent_system.fundamental_agent import FundamentalAgent
from multi_agent_system.strategy_agent import StrategyAgent


def _fin(gm=40.0, nm=20.0):
    return FinancialsSnapshot(
        stock_id="2330", roc_year=115, season=1, eps=5.0,
        revenue_k=1_000_000.0, gross_margin_pct=gm, net_margin_pct=nm,
    )


# ------------------------------------------------------------------ 評分
def test_all_components_max_score():
    v = FundamentalAgent().evaluate(_fin(gm=40, nm=20), revenue_yoy_pct=30.0)
    assert v.available and v.score == pytest.approx(1.0)     # 三分量皆頂 → 1.0


def test_all_components_min_score():
    v = FundamentalAgent().evaluate(_fin(gm=0, nm=0), revenue_yoy_pct=-20.0)
    assert v.score == pytest.approx(0.0)


def test_revenue_missing_renormalizes_over_margins():
    # 毛利率 20%（[0,40]→0.5）、淨利率 10%（[0,20]→0.5）、無月營收
    # → (0.3*0.5 + 0.4*0.5) / (0.3+0.4) = 0.5
    v = FundamentalAgent().evaluate(_fin(gm=20, nm=10), revenue_yoy_pct=None)
    assert v.score == pytest.approx(0.5)
    assert "僅財報" in v.reason


def test_no_financials_unavailable():
    v = FundamentalAgent().evaluate(None)
    assert v.available is False and v.score is None


def test_all_margins_missing_unavailable():
    fin = FinancialsSnapshot("2330", 115, 1, None, None, None, None)
    v = FundamentalAgent().evaluate(fin, revenue_yoy_pct=None)
    assert v.available is False


# ------------------------------------------------------------------ 融合：選填專家
def _v(agent, score):
    return AgentVerdict(agent, True, score, f"{agent} ok", {})


def test_fusion_fundamental_absent_reproduces_legacy_weights():
    # 不給基本面 → 就三專家重新歸一化，須精確等於舊 0.30/0.50/0.20 權重結果。
    d = StrategyAgent().decide("2330", _v("M", 0.8), _v("T", 0.9), _v("A", 0.7))
    assert d.final_score == pytest.approx(0.83)     # 0.30*0.8 + 0.50*0.9 + 0.20*0.7
    assert not d.abstained


def test_fusion_with_fundamental_uses_four_weights():
    # 四專家皆 0.8 → 加權平均仍 0.8（權重總和 1.0）。
    d = StrategyAgent().decide(
        "2330", _v("M", 0.8), _v("T", 0.8), _v("A", 0.8), fundamental=_v("F", 0.8)
    )
    assert d.final_score == pytest.approx(0.8)
    assert "基本面" in d.rationale


def test_fusion_missing_fundamental_does_not_abstain():
    # 基本面 unavailable（ETF 無財報）+ 三必需齊 → 照常決策，不 abstain。
    fund = AgentVerdict.unavailable("FundamentalAgent", "查無財報")
    d = StrategyAgent().decide("0050", _v("M", 0.8), _v("T", 0.9), _v("A", 0.7), fundamental=fund)
    assert not d.abstained
    assert d.final_score == pytest.approx(0.83)     # 與缺席時一致
    assert "基本面" not in d.rationale               # 選填缺席 → 不列該行


# ------------------------------------------------------------------ 月營收 YoY 讀取
def _stock_db(path, rows) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE monthly_revenue (stock_id TEXT, date TEXT, revenue REAL)")
        conn.executemany("INSERT INTO monthly_revenue VALUES (?,?,?)", rows)


def _agent(stock_db):
    return DataAggregationAgent(str(stock_db), "f.db", "n.db")


def test_revenue_yoy_latest_vs_prior_year(tmp_path):
    p = tmp_path / "stock.db"
    _stock_db(p, [
        ("2330", "2025-06-10", 100.0),
        ("2330", "2026-06-10", 120.0),   # 最新 → vs 去年同月 100 → +20%
    ])
    assert _agent(p)._fetch_revenue_yoy("2330", []) == pytest.approx(20.0)


def test_revenue_yoy_missing_table_none(tmp_path):
    p = tmp_path / "stock.db"
    with sqlite3.connect(str(p)) as conn:
        conn.execute("CREATE TABLE other (x INT)")
    assert _agent(p)._fetch_revenue_yoy("2330", []) is None


def test_revenue_yoy_no_prior_base_none(tmp_path):
    p = tmp_path / "stock.db"
    _stock_db(p, [("2330", "2026-06-10", 120.0)])       # 無去年同月
    assert _agent(p)._fetch_revenue_yoy("2330", []) is None


def test_revenue_yoy_nonpositive_base_none(tmp_path):
    p = tmp_path / "stock.db"
    warns: list[str] = []
    _stock_db(p, [("2330", "2025-06-10", 0.0), ("2330", "2026-06-10", 120.0)])
    assert _agent(p)._fetch_revenue_yoy("2330", warns) is None
    assert warns                                         # 基期<=0 有告警
