"""seed_demo_dbs.py — 建立三個「示範用」SQLite 資料庫，讓系統可離線跑通。

⚠️ 這是 DEMO / 範例資料，物理隔離於 demo_data/ 目錄，
   與使用者真實 stock.db / fund.db / news.db 完全分離（對照 CLAUDE.md：測試資料不得流入正式路徑）。

Schema（對齊使用者規格）
------------------------
    stock_technical(date, stock_id, close, rsi, upper_band, lower_band,   # my-stock-dashboard
                    ma20, ma60, kd_k, kd_d,
                    foreign_net_lots, trust_net_lots, total_net_lots)     # 均線/KD/籌碼(張)
    macro_tw_pmi(date, pmi, label, source)                                # 台股 PMI（榮枯線 50）
    institutional_flow(date, foreign_buy)                                 # 外資買賣超（億元，賣超為負）
    us_market(date, us_stock_id, close)                                   # my-Fund-dashboard
    fred_macro(date, series_id, value)                                    # 美股/全球總經（利差 + CPI）
    news(date, title, content, sentiment_score)                          # mynews

用法
----
    python scripts/seed_demo_dbs.py           # 產生於 <repo>/demo_data/
"""

from __future__ import annotations

import os
import sqlite3

# --- DEMO 資料 --------------------------------------------------------------
# D3「順勢 + 回檔進場」示範（技術面判斷已改 trend×timing 交互項，非純均值回歸）：
# 2330：上升趨勢（close>MA20>MA60）中的「回檔 + 法人買超」→ D3 最佳買點 → 技術面偏多。
# 2454：下跌趨勢（close<MA20<MA60）中的「反彈至上軌 + 法人賣超」→ 不接刀（entry=trend×timing≈0）→ 技術面偏空。
_STOCK_ROWS = [
    # (date, stock_id, close, rsi, upper_band, lower_band,
    #  ma20, ma60, kd_k, kd_d, foreign_net_lots, trust_net_lots, total_net_lots)  # 均線=元、籌碼=張
    # 2330：均線多頭排列（MA20>MA60、close 在上），近期回檔（%B/RSI 走低）、法人持續買超。
    ("2026-07-14", "2330", 940.0, 52.0, 1005.0, 925.0, 930.0, 915.0, 60.0, 55.0, 4000.0, 250.0, 4600.0),
    ("2026-07-15", "2330", 958.0, 58.0, 1015.0, 930.0, 940.0, 920.0, 68.0, 62.0, 5200.0, 300.0, 6000.0),
    ("2026-07-16", "2330", 972.0, 55.0, 1025.0, 940.0, 948.0, 926.0, 62.0, 64.0, 6800.0, 350.0, 7800.0),
    ("2026-07-17", "2330", 990.0, 58.0, 1030.0, 946.0, 954.0, 930.0, 64.0, 60.0, 9000.0, 420.0, 10300.0),
    # 最新：順勢回檔（close 972 仍 > MA20 960 > MA60 935，但 %B≈0.28 / RSI 44 走低）+ KD 黃金交叉翻揚 + 法人買超 → D3 偏多。
    ("2026-07-18", "2330", 972.0, 44.0, 1030.0, 950.0, 960.0, 935.0, 48.0, 42.0, 10800.0, 512.0, 12480.0),
    # 2454：均線空頭排列（MA20<MA60、close 在下），近期反彈至上軌（%B/RSI 走高）、法人持續賣超。
    ("2026-07-16", "2454", 1360.0, 60.0, 1400.0, 1250.0, 1370.0, 1400.0, 55.0, 60.0, -3000.0, -100.0, -3500.0),
    ("2026-07-17", "2454", 1335.0, 66.0, 1345.0, 1200.0, 1352.0, 1385.0, 70.0, 64.0, -5500.0, -150.0, -6200.0),
    # 最新：逆勢反彈（close 1310 仍 < MA20 1340 < MA60 1370，但 %B≈0.87 貼上軌 / RSI 72 過熱）+ KD 死叉 + 法人賣超 → 不接刀 → D3 偏空。
    ("2026-07-18", "2454", 1310.0, 72.0, 1330.0, 1180.0, 1340.0, 1370.0, 82.0, 85.0, -8120.0, -230.0, -9450.0),
]

# 台股 PMI（指數點位，榮枯線 50）—— 最新 55.3 → 擴張。
_TW_PMI_ROWS = [
    # (date, pmi, label, source)
    ("2026-05-01", 53.8, "中華經濟研究院 PMI（2026-05 官方公布）", "DEMO"),
    ("2026-06-01", 55.3, "中華經濟研究院 PMI（2026-06 官方公布）", "DEMO"),
]

# 外資買賣超（億元，賣超為負）—— 最新 -60.8 億 → 賣超。
_TW_INST_ROWS = [
    # (date, foreign_buy)
    ("2026-07-16", -48.3),
    ("2026-07-17", 25.6),
    ("2026-07-18", -60.8),
]

# 盤前訊號：台指期外資留倉（口，+多/-空）+ 台指夜盤收盤/漲跌（點, %）。
_TW_FUT_OI_ROWS = [
    # (date, foreign_net_oi_lots)
    ("2026-07-17", 9800.0),
    ("2026-07-18", 12480.0),      # 最新 → +12,480 口（偏多）
]
_TW_FUT_NIGHT_ROWS = [
    # (date, night_close, day_close, chg_pts, chg_pct)
    ("2026-07-17", 22065.0, 22010.0, 55.0, 0.25),
    ("2026-07-18", 22150.0, 22065.0, 85.0, 0.385),   # 最新 → +85 點 / +0.4% → 小漲（偏多）
]

_US_ROWS = [
    # (date, us_stock_id, close)
    ("2026-07-16", "NVDA", 172.0),
    ("2026-07-17", "NVDA", 175.5),
    ("2026-07-18", "NVDA", 178.2),
    ("2026-07-18", "AMD", 168.0),
]

# 美股/全球總經（FRED series；DGS10/DGS2 日頻 %、CPIAUCSL 月頻指數點）。
# 最新利差 = 4.55 − 4.12 = +0.43%（正常）；CPI YoY = (331.0/321.0 − 1)×100 ≈ 3.1%（溫和）。
_FRED_ROWS = [
    # (date, series_id, value)
    ("2026-07-17", "DGS10", 4.58),
    ("2026-07-18", "DGS10", 4.55),
    ("2026-07-17", "DGS2", 4.15),
    ("2026-07-18", "DGS2", 4.12),
    ("2025-07-01", "CPIAUCSL", 321.0),   # 12 月前基期
    ("2026-07-01", "CPIAUCSL", 331.0),   # 最新月
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
            "rsi REAL, upper_band REAL, lower_band REAL, "
            "ma20 REAL, ma60 REAL, kd_k REAL, kd_d REAL, "
            "foreign_net_lots REAL, trust_net_lots REAL, total_net_lots REAL)"
        )
        conn.executemany(
            "INSERT INTO stock_technical VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", _STOCK_ROWS
        )
        # 台股總經（PMI + 外資買賣超）—— 供市場快訊「台股情勢」。
        conn.execute("DROP TABLE IF EXISTS macro_tw_pmi")
        conn.execute(
            "CREATE TABLE macro_tw_pmi (date TEXT, pmi REAL, label TEXT, source TEXT)"
        )
        conn.executemany("INSERT INTO macro_tw_pmi VALUES (?,?,?,?)", _TW_PMI_ROWS)
        conn.execute("DROP TABLE IF EXISTS institutional_flow")
        conn.execute("CREATE TABLE institutional_flow (date TEXT, foreign_buy REAL)")
        conn.executemany("INSERT INTO institutional_flow VALUES (?,?)", _TW_INST_ROWS)
        # 盤前夜盤（B：台指期外資留倉 + 台指夜盤漲跌）。
        conn.execute("DROP TABLE IF EXISTS futures_oi")
        conn.execute("CREATE TABLE futures_oi (date TEXT, foreign_net_oi_lots REAL)")
        conn.executemany("INSERT INTO futures_oi VALUES (?,?)", _TW_FUT_OI_ROWS)
        conn.execute("DROP TABLE IF EXISTS futures_night")
        conn.execute(
            "CREATE TABLE futures_night "
            "(date TEXT, night_close REAL, day_close REAL, chg_pts REAL, chg_pct REAL)"
        )
        conn.executemany("INSERT INTO futures_night VALUES (?,?,?,?,?)", _TW_FUT_NIGHT_ROWS)


def _create_fund_db(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE IF EXISTS us_market")
        conn.execute(
            "CREATE TABLE us_market ("
            "date TEXT NOT NULL, us_stock_id TEXT NOT NULL, close REAL)"
        )
        conn.executemany("INSERT INTO us_market VALUES (?,?,?)", _US_ROWS)
        # 美股/全球總經（利差 + CPI）—— 供市場快訊「國際情勢」+ 總經專家評分。
        conn.execute("DROP TABLE IF EXISTS fred_macro")
        conn.execute("CREATE TABLE fred_macro (date TEXT, series_id TEXT, value REAL)")
        conn.executemany("INSERT INTO fred_macro VALUES (?,?,?)", _FRED_ROWS)


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
