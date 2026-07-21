"""macro_db.py — 從 fund.db / stock.db 讀「真實」總經（資料層，無 UI、無外部 HTTP）。

單一職責
--------
把三庫中的**總經**（非個股）讀成系統內契約物件：
* `read_us_macro(fund_db)`  → `MacroReading`（美股/全球：10Y-2Y 利差 + CPI 年增率）。
* `read_tw_macro(stock_db)` → `TwMacroReading`（台股：PMI 榮枯 + 外資買賣超 億元）。

資料來源（皆為各來源專案已 export 的離線層，免 API key、免即時網路）
------------------------------------------------------------------
    fund.db  fred_macro(date, series_id, value)     ← my-Fund-dashboard
        DGS10 / DGS2  日頻公債殖利率（%）；CPIAUCSL 月頻 CPI 指數（點）。
    stock.db macro_tw_pmi(date, pmi, label, source) ← my-stock-dashboard
             institutional_flow(date, foreign_buy)  ← 外資買賣超（億元，賣超為負）。

數學
----
    yield_spread_pct = DGS10(最新) − DGS2(最新)              （單位百分點）
    cpi_yoy_pct      = (CPIAUCSL_t / CPIAUCSL_{t−12月} − 1) × 100   （單位百分點）

Fail-Loud（對照使用者 CLAUDE.md §1）
-----------------------------------
* `read_us_macro`：利差或 CPI 年增率任一算不出（series 缺、12 月前無對應月）→ `raise
  DataSourceError`。**絕不**回預設值 / 假數字讓流程「看起來成功」；由呼叫端決定是否降級
  （見 run_pipeline._build_macro_provider：真實讀不到才退回 env / 模擬並印警語）。
* `read_tw_macro`：PMI 與外資**各自**可缺（回 None），但 DB 檔開不了 → `raise`（不靜默）。
"""

from __future__ import annotations

import sqlite3

from .contracts import MacroReading, TwMacroReading, TwNightReading
from .data_agent import DataSourceError, _connect_readonly


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _latest(conn: sqlite3.Connection, series_id: str) -> tuple[str, float] | None:
    """fred_macro 中某 series 的最新一筆 (date, value)；查無 → None。"""
    row = conn.execute(
        "SELECT date, value FROM fred_macro WHERE series_id = ? AND value IS NOT NULL "
        "ORDER BY date DESC LIMIT 1",
        (series_id,),
    ).fetchone()
    return (str(row[0]), float(row[1])) if row else None


def _twelve_months_prior_iso(iso_month: str) -> str:
    """'YYYY-MM-01' → 12 個月前的 'YYYY-MM-01'（CPI 為月頻，錨定月初）。"""
    y, m = int(iso_month[:4]), int(iso_month[5:7])
    return f"{y - 1:04d}-{m:02d}-01"


def read_us_macro(fund_db_path: str) -> MacroReading:
    """讀 fund.db fred_macro → 美股/全球總經 MacroReading（is_simulated=False）。

    Fail-Loud：DGS10/DGS2 任一缺、或 CPI 無法算年增率 → raise DataSourceError。
    """
    with _connect_readonly(fund_db_path) as conn:
        if not _table_exists(conn, "fred_macro"):
            raise DataSourceError(f"fund.db 無 fred_macro 表：{fund_db_path}")

        dgs10 = _latest(conn, "DGS10")
        dgs2 = _latest(conn, "DGS2")
        if dgs10 is None or dgs2 is None:
            missing = [s for s, v in (("DGS10", dgs10), ("DGS2", dgs2)) if v is None]
            raise DataSourceError(f"fred_macro 缺殖利率 series {missing}，無法算利差")

        # 各取最新（日頻，通常同日）；血緣 as_of 取兩者較新者。
        yield_spread_pct = dgs10[1] - dgs2[1]

        cpi_latest = _latest(conn, "CPIAUCSL")
        if cpi_latest is None:
            raise DataSourceError("fred_macro 缺 CPIAUCSL，無法算 CPI 年增率")
        prior_iso = _twelve_months_prior_iso(cpi_latest[0])
        prior_row = conn.execute(
            "SELECT value FROM fred_macro WHERE series_id='CPIAUCSL' AND date = ?",
            (prior_iso,),
        ).fetchone()
        if prior_row is None or prior_row[0] is None:
            raise DataSourceError(
                f"CPIAUCSL 缺 {prior_iso}（{cpi_latest[0]} 的 12 月前基期），無法算年增率"
            )
        cpi_yoy_pct = (cpi_latest[1] / float(prior_row[0]) - 1.0) * 100.0

    as_of = max(dgs10[0], dgs2[0], cpi_latest[0])
    return MacroReading(
        yield_spread_pct=yield_spread_pct,
        cpi_yoy_pct=cpi_yoy_pct,
        source="fund.db:fred_macro(DGS10-DGS2,CPIAUCSL)",
        as_of=as_of,
        is_simulated=False,
    )


def read_tw_macro(stock_db_path: str) -> TwMacroReading:
    """讀 stock.db → 台股總經 TwMacroReading（PMI + 外資，各自可缺）。

    PMI 取 macro_tw_pmi 最新月；外資取 institutional_flow.foreign_buy 最新交易日（億元）。
    某表缺 / 無資料 → 該欄 None（顯示「資料不足」，不捏造）；DB 開不了 → raise。
    """
    pmi = pmi_as_of = None
    foreign_net_yi = foreign_as_of = None

    with _connect_readonly(stock_db_path) as conn:
        if _table_exists(conn, "macro_tw_pmi"):
            row = conn.execute(
                "SELECT date, pmi FROM macro_tw_pmi WHERE pmi IS NOT NULL "
                "ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                pmi_as_of, pmi = str(row[0]), float(row[1])

        if _table_exists(conn, "institutional_flow"):
            row = conn.execute(
                "SELECT date, foreign_buy FROM institutional_flow WHERE foreign_buy IS NOT NULL "
                "ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                foreign_as_of, foreign_net_yi = str(row[0]), float(row[1])

    return TwMacroReading(
        pmi=pmi,
        pmi_as_of=pmi_as_of,
        foreign_net_yi=foreign_net_yi,
        foreign_as_of=foreign_as_of,
        source="stock.db:macro_tw_pmi+institutional_flow",
        is_simulated=False,
    )


def read_tw_night(stock_db_path: str) -> TwNightReading:
    """讀 stock.db → 台股盤前訊號（外資期貨留倉 + 台指夜盤漲跌）。各自可缺 → None。

    * futures_oi(date, foreign_net_oi_lots)      外資期貨留倉淨口數（口）。
    * futures_night(date, night_close, chg_pts, chg_pct)  台指夜盤收盤 + 相對日盤漲跌。
    表為 live 層（需 FINMIND_TOKEN 才落地）→ 表缺時該段回 None（不炸、不捏造）。
    """
    oi = oi_as_of = None
    night_close = night_pts = night_pct = night_as_of = None

    with _connect_readonly(stock_db_path) as conn:
        if _table_exists(conn, "futures_oi"):
            row = conn.execute(
                "SELECT date, foreign_net_oi_lots FROM futures_oi "
                "WHERE foreign_net_oi_lots IS NOT NULL ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                oi_as_of, oi = str(row[0]), float(row[1])

        if _table_exists(conn, "futures_night"):
            row = conn.execute(
                "SELECT date, night_close, chg_pts, chg_pct FROM futures_night "
                "WHERE night_close IS NOT NULL ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                night_as_of = str(row[0])
                night_close = float(row[1])
                night_pts = None if row[2] is None else float(row[2])
                night_pct = None if row[3] is None else float(row[3])

    return TwNightReading(
        foreign_fut_oi_lots=oi,
        fut_oi_as_of=oi_as_of,
        night_close=night_close,
        night_chg_pts=night_pts,
        night_chg_pct=night_pct,
        night_as_of=night_as_of,
        source="stock.db:futures_oi+futures_night",
        is_simulated=False,
    )
