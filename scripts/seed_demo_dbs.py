"""seed_demo_dbs.py — 建立三個「示範用」SQLite 資料庫，讓系統可離線跑通。

⚠️ 這是 DEMO / 範例資料，物理隔離於 demo_data/ 目錄，
   與使用者真實 stock.db / fund.db / news.db 完全分離（對照 CLAUDE.md：測試資料不得流入正式路徑）。

Schema（對齊使用者規格）
------------------------
    stock_technical(date, stock_id, close, rsi, upper_band, lower_band)   # my-stock-dashboard
    us_market(date, us_stock_id, close)                                   # my-Fund-dashboard
    news(date, title, content, sentiment_score)                          # mynews

用法
----
    python scripts/seed_demo_dbs.py           # 產生於 <repo>/demo_data/
"""

from __future__ import annotations

import os
import sqlite3

# --- DEMO 資料 --------------------------------------------------------------
# 2330：最新一期「超賣便宜」（RSI 低、收盤貼近下軌）→ 技術面偏多。
# 2454：最新一期「超買昂貴」（RSI 高、收盤貼近上軌）→ 技術面偏空。
_STOCK_ROWS = [
    # (date, stock_id, close, rsi, upper_band, lower_band)
    ("2026-07-14", "2330", 960.0, 45.0, 1010.0, 930.0),
    ("2026-07-15", "2330", 945.0, 38.0, 1008.0, 928.0),
    ("2026-07-16", "2330", 935.0, 33.0, 1006.0, 926.0),
    ("2026-07-17", "2330", 928.0, 30.0, 1005.0, 925.0),
    ("2026-07-18", "2330", 927.0, 28.0, 1004.0, 924.0),   # 最新：貼下軌、RSI 28 超賣
    ("2026-07-16", "2454", 1300.0, 68.0, 1360.0, 1180.0),
    ("2026-07-17", "2454", 1345.0, 72.0, 1365.0, 1185.0),
    ("2026-07-18", "2454", 1358.0, 76.0, 1366.0, 1186.0),  # 最新：貼上軌、RSI 76 超買
]

_US_ROWS = [
    # (date, us_stock_id, close)
    ("2026-07-16", "NVDA", 172.0),
    ("2026-07-17", "NVDA", 175.5),
    ("2026-07-18", "NVDA", 178.2),
    ("2026-07-18", "AMD", 168.0),
]

_NEWS_ROWS = [
    # (date, title, content, sentiment_score)  sentiment ∈ [-1, 1]
    ("2026-07-18", "台積電先進製程需求強勁", "台積電 2 奈米訂單滿載，AI 晶片帶動半導體景氣。", 0.72),
    ("2026-07-17", "外資買超台積電", "外資連三日買超台積電，看好 TSMC 長線競爭力。", 0.55),
    ("2026-07-16", "半導體庫存調整雜音", "部分半導體廠傳庫存調整，短線波動加大。", -0.30),
    ("2026-07-15", "NVDA 財報優於預期", "輝達資料中心營收創高，連動台鏈記憶體與晶圓代工。", 0.61),
    ("2026-07-10", "舊聞：淡季展望保守", "此則超過 7 天視窗，理論上不應被最新查詢命中。", -0.10),
]


def _create_stock_db(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE IF EXISTS stock_technical")
        conn.execute(
            "CREATE TABLE stock_technical ("
            "date TEXT NOT NULL, stock_id TEXT NOT NULL, close REAL, "
            "rsi REAL, upper_band REAL, lower_band REAL)"
        )
        conn.executemany(
            "INSERT INTO stock_technical VALUES (?,?,?,?,?,?)", _STOCK_ROWS
        )


def _create_fund_db(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE IF EXISTS us_market")
        conn.execute(
            "CREATE TABLE us_market ("
            "date TEXT NOT NULL, us_stock_id TEXT NOT NULL, close REAL)"
        )
        conn.executemany("INSERT INTO us_market VALUES (?,?,?)", _US_ROWS)


def _create_news_db(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE IF EXISTS news")
        conn.execute(
            "CREATE TABLE news ("
            "date TEXT NOT NULL, title TEXT, content TEXT, sentiment_score REAL)"
        )
        conn.executemany("INSERT INTO news VALUES (?,?,?,?)", _NEWS_ROWS)


def seed_all(demo_dir: str) -> dict[str, str]:
    """建立三個 demo DB，回傳 {stock_db, fund_db, news_db} 路徑。"""
    os.makedirs(demo_dir, exist_ok=True)
    paths = {
        "stock_db": os.path.join(demo_dir, "stock.db"),
        "fund_db": os.path.join(demo_dir, "fund.db"),
        "news_db": os.path.join(demo_dir, "news.db"),
    }
    _create_stock_db(paths["stock_db"])
    _create_fund_db(paths["fund_db"])
    _create_news_db(paths["news_db"])
    return paths


def default_demo_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo_data")


if __name__ == "__main__":
    out = seed_all(default_demo_dir())
    print("✅ 已建立 DEMO 資料庫：")
    for k, v in out.items():
        print(f"  {k}: {v}")
