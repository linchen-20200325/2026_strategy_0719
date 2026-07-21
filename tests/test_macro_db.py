"""test_macro_db.py — 三庫總經讀取（fund.db 利差/CPI + stock.db PMI/外資）。

覆蓋：正常算式、Fail-Loud（缺 series / 缺 12 月前基期 → raise）、
台股各指標獨立可缺（缺表 → None，不炸）。
"""

from __future__ import annotations

import sqlite3

import pytest

from multi_agent_system.data_agent import DataSourceError
from multi_agent_system.macro_db import read_tw_macro, read_tw_night, read_us_macro


# ------------------------------------------------------------------ fixtures
def _make_fund_db(path, fred_rows) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE fred_macro (date TEXT, series_id TEXT, value REAL)")
        conn.executemany("INSERT INTO fred_macro VALUES (?,?,?)", fred_rows)


def _make_stock_db(path, *, pmi_rows=None, inst_rows=None) -> None:
    with sqlite3.connect(path) as conn:
        if pmi_rows is not None:
            conn.execute("CREATE TABLE macro_tw_pmi (date TEXT, pmi REAL, label TEXT, source TEXT)")
            conn.executemany("INSERT INTO macro_tw_pmi VALUES (?,?,?,?)", pmi_rows)
        if inst_rows is not None:
            conn.execute("CREATE TABLE institutional_flow (date TEXT, foreign_buy REAL)")
            conn.executemany("INSERT INTO institutional_flow VALUES (?,?)", inst_rows)


_GOOD_FRED = [
    ("2026-07-17", "DGS10", 4.58),
    ("2026-07-18", "DGS10", 4.55),
    ("2026-07-17", "DGS2", 4.15),
    ("2026-07-18", "DGS2", 4.12),
    ("2025-07-01", "CPIAUCSL", 320.0),
    ("2026-07-01", "CPIAUCSL", 330.0),
]


# ------------------------------------------------------------------ read_us_macro
def test_read_us_macro_spread_and_cpi_yoy(tmp_path):
    p = tmp_path / "fund.db"
    _make_fund_db(p, _GOOD_FRED)
    r = read_us_macro(str(p))
    assert r.yield_spread_pct == pytest.approx(4.55 - 4.12)     # 各取最新
    assert r.cpi_yoy_pct == pytest.approx((330.0 / 320.0 - 1) * 100)  # 12 月前基期
    assert r.is_simulated is False
    assert r.as_of == "2026-07-18"
    assert "fred_macro" in r.source


def test_read_us_macro_missing_series_raises(tmp_path):
    p = tmp_path / "fund.db"
    _make_fund_db(p, [("2026-07-18", "DGS10", 4.55)])  # 缺 DGS2 + CPI
    with pytest.raises(DataSourceError, match="DGS2"):
        read_us_macro(str(p))


def test_read_us_macro_missing_cpi_base_raises(tmp_path):
    # 有利差、有最新 CPI，但缺 12 月前基期 → Fail-Loud（不硬算/不回假）。
    p = tmp_path / "fund.db"
    _make_fund_db(p, [
        ("2026-07-18", "DGS10", 4.55),
        ("2026-07-18", "DGS2", 4.12),
        ("2026-07-01", "CPIAUCSL", 330.0),  # 無 2025-07-01
    ])
    with pytest.raises(DataSourceError, match="12 月前基期"):
        read_us_macro(str(p))


def test_read_us_macro_no_table_raises(tmp_path):
    p = tmp_path / "fund.db"
    with sqlite3.connect(str(p)) as conn:
        conn.execute("CREATE TABLE other (x INT)")
    with pytest.raises(DataSourceError, match="無 fred_macro"):
        read_us_macro(str(p))


# ------------------------------------------------------------------ read_tw_macro
def test_read_tw_macro_both_present(tmp_path):
    p = tmp_path / "stock.db"
    _make_stock_db(
        p,
        pmi_rows=[("2026-05-01", 53.8, "L", "S"), ("2026-06-01", 55.3, "L", "S")],
        inst_rows=[("2026-07-17", 25.6), ("2026-07-18", -60.8)],
    )
    r = read_tw_macro(str(p))
    assert r.pmi == pytest.approx(55.3)          # 最新月
    assert r.pmi_as_of == "2026-06-01"
    assert r.foreign_net_yi == pytest.approx(-60.8)  # 最新交易日
    assert r.foreign_as_of == "2026-07-18"
    assert r.is_simulated is False


def test_read_tw_macro_missing_indicators_are_none(tmp_path):
    # 只有 PMI 表、無外資表 → 外資 None（不炸、不捏造）。
    p = tmp_path / "stock.db"
    _make_stock_db(p, pmi_rows=[("2026-06-01", 55.3, "L", "S")])
    r = read_tw_macro(str(p))
    assert r.pmi == pytest.approx(55.3)
    assert r.foreign_net_yi is None
    assert r.foreign_as_of is None


def test_read_tw_macro_empty_db_all_none(tmp_path):
    # 兩表皆缺（如 live 層未落地）→ 全 None，仍回一個誠實的 reading。
    p = tmp_path / "stock.db"
    with sqlite3.connect(str(p)) as conn:
        conn.execute("CREATE TABLE stock_technical (x INT)")
    r = read_tw_macro(str(p))
    assert r.pmi is None and r.foreign_net_yi is None


def test_read_tw_macro_missing_file_raises(tmp_path):
    with pytest.raises(DataSourceError):
        read_tw_macro(str(tmp_path / "nope.db"))


# ------------------------------------------------------------------ read_tw_night
def test_read_tw_night_both_present(tmp_path):
    p = tmp_path / "stock.db"
    with sqlite3.connect(str(p)) as conn:
        conn.execute("CREATE TABLE futures_oi (date TEXT, foreign_net_oi_lots REAL)")
        conn.executemany("INSERT INTO futures_oi VALUES (?,?)",
                         [("2026-07-17", 9800.0), ("2026-07-18", 12480.0)])
        conn.execute("CREATE TABLE futures_night "
                     "(date TEXT, night_close REAL, day_close REAL, chg_pts REAL, chg_pct REAL)")
        conn.executemany("INSERT INTO futures_night VALUES (?,?,?,?,?)",
                         [("2026-07-18", 22150.0, 22065.0, 85.0, 0.385)])
    r = read_tw_night(str(p))
    assert r.foreign_fut_oi_lots == pytest.approx(12480.0)   # 最新
    assert r.night_close == pytest.approx(22150.0)
    assert r.night_chg_pct == pytest.approx(0.385)
    assert r.is_simulated is False


def test_read_tw_night_missing_tables_all_none(tmp_path):
    p = tmp_path / "stock.db"
    with sqlite3.connect(str(p)) as conn:
        conn.execute("CREATE TABLE other (x INT)")
    r = read_tw_night(str(p))
    assert r.foreign_fut_oi_lots is None and r.night_close is None


def test_read_tw_night_only_oi(tmp_path):
    p = tmp_path / "stock.db"
    with sqlite3.connect(str(p)) as conn:
        conn.execute("CREATE TABLE futures_oi (date TEXT, foreign_net_oi_lots REAL)")
        conn.executemany("INSERT INTO futures_oi VALUES (?,?)", [("2026-07-18", -3300.0)])
    r = read_tw_night(str(p))
    assert r.foreign_fut_oi_lots == pytest.approx(-3300.0)
    assert r.night_close is None
