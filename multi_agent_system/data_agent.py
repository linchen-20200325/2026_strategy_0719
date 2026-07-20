"""data_agent.py — 資料代理人 (Data Aggregation Agent)。

單一職責
--------
接收「台股代號 / 連動美股代號 / 新聞關鍵字清單」，對三個獨立 SQLite 資料庫
做跨庫查詢，抓取最新一期技術面、美股連動面，以及過去 N 天相關新聞，
打包成標準 `DataPacket`（可轉 JSON）。

資料流向
--------
    stock.db  ─┐
    fund.db   ─┼─► DataAggregationAgent.aggregate() ─► DataPacket ─► 各專家
    news.db   ─┘

失敗降級策略（對照 CLAUDE.md §1 Fail Loud）
------------------------------------------
* 資料庫檔不存在 / 連線失敗 / 資料表缺欄位 → `raise DataSourceError`（大聲炸，不吞）。
* 查無該股票 / 查無新聞 → 對應欄位回 None / 空，並在 packet.warnings 明確記錄，
  絕不用 fillna(0) 或假資料掩蓋。
* 來源 DB 一律以唯讀 (mode=ro) 開啟，杜絕代理人意外污染上游資料。
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Sequence
from datetime import date, timedelta

import pandas as pd

from config import (
    NEWS_LOOKBACK_DAYS,
    SENTIMENT_RAW_MAX,
    SENTIMENT_RAW_MIN,
)

from .contracts import (
    DataPacket,
    NewsItem,
    TechnicalSnapshot,
    UsLinkSnapshot,
)
from .numerics import clamp


class DataSourceError(RuntimeError):
    """資料來源層級的致命錯誤（DB 缺檔 / 連線斷 / schema 不符）。"""


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_identifier(name: str, kind: str) -> str:
    """驗證資料表名為安全識別字（表名無法參數化，須防 SQL injection）。"""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"不合法的{kind}名稱：{name!r}（僅允許英數與底線）")
    return name


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    """以唯讀模式開啟 SQLite；檔案不存在時給出明確錯誤（Fail Loud）。"""
    if not os.path.exists(db_path):
        raise DataSourceError(f"資料庫檔不存在：{db_path}")
    try:
        # uri=True + mode=ro：保證不會對來源庫寫入。
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:  # 連線層錯誤
        raise DataSourceError(f"無法連線資料庫 {db_path}：{exc}") from exc


def _read_sql(conn: sqlite3.Connection, sql: str, params: Sequence) -> pd.DataFrame:
    """執行查詢並把 sqlite/pandas 錯誤統一轉為 DataSourceError。"""
    try:
        return pd.read_sql_query(sql, conn, params=list(params))
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise DataSourceError(f"查詢失敗：{exc}\nSQL={sql}") from exc


class DataAggregationAgent:
    """跨三庫資料整合代理人。"""

    def __init__(
        self,
        stock_db: str,
        fund_db: str,
        news_db: str,
        *,
        stock_table: str = "stock_technical",
        us_table: str = "us_market",
        news_table: str = "news",
    ) -> None:
        self.stock_db = stock_db
        self.fund_db = fund_db
        self.news_db = news_db
        # 表名可設定以對接使用者真實 schema；經識別字白名單驗證防注入。
        self.stock_table = _safe_identifier(stock_table, "stock 資料表")
        self.us_table = _safe_identifier(us_table, "us 資料表")
        self.news_table = _safe_identifier(news_table, "news 資料表")

    # ---------------------------------------------------------------- public
    def aggregate(
        self,
        tw_stock_id: str,
        us_stock_id: str,
        news_keywords: Sequence[str],
        *,
        lookback_days: int = NEWS_LOOKBACK_DAYS,
        as_of_date: date | None = None,
    ) -> DataPacket:
        """跨庫抓取並打包。

        Parameters
        ----------
        tw_stock_id   : 台股代號（對 stock.db）
        us_stock_id   : 連動美股/基金代號（對 fund.db）
        news_keywords : 新聞關鍵字清單（title/content 任一命中即納入）
        lookback_days : 新聞回溯天數（預設 config.NEWS_LOOKBACK_DAYS = 7）
        as_of_date    : 觀察基準日；新聞窗 = [as_of - lookback_days, as_of]。
                        預設今天 (UTC)。顯式參數化以利回測/測試可重現。
        """
        if not tw_stock_id:
            raise ValueError("tw_stock_id 不可為空")
        if lookback_days <= 0:
            raise ValueError(f"lookback_days 必須為正整數，收到 {lookback_days}")

        as_of = as_of_date or date.today()
        warnings: list[str] = []

        technical = self._fetch_technical(tw_stock_id, warnings)
        us_link = self._fetch_us_link(us_stock_id, warnings)
        news = self._fetch_news(news_keywords, as_of, lookback_days, warnings)

        sentiment_mean = None
        if news:
            sentiment_mean = sum(n.sentiment_score for n in news) / len(news)

        return DataPacket(
            tw_stock_id=tw_stock_id,
            technical=technical,
            us_link=us_link,
            news=tuple(news),
            news_sentiment_mean=sentiment_mean,
            news_count=len(news),
            warnings=tuple(warnings),
        )

    def fetch_news(
        self,
        news_keywords: Sequence[str],
        *,
        lookback_days: int = NEWS_LOOKBACK_DAYS,
        as_of_date: date | None = None,
    ) -> list[NewsItem]:
        """市場級新聞查詢（不綁單一個股）——供市場快訊統計國際 / 台股情緒。

        沿用內部 `_fetch_news`（title/content 命中關鍵字即納入）,窗 = [as_of - lookback, as_of]。
        """
        as_of = as_of_date or date.today()
        warnings: list[str] = []
        return self._fetch_news(news_keywords, as_of, lookback_days, warnings)

    # --------------------------------------------------------------- private
    def _fetch_technical(
        self, tw_stock_id: str, warnings: list[str]
    ) -> TechnicalSnapshot | None:
        # SELECT *：向後相容 —— 舊 stock.db 只有 6 欄,新版含均線/KD/籌碼;缺欄 → 該欄 None。
        sql = (
            f"SELECT * FROM {self.stock_table} WHERE stock_id = ? "
            "ORDER BY date DESC LIMIT 1"
        )
        with _connect_readonly(self.stock_db) as conn:
            df = _read_sql(conn, sql, [tw_stock_id])
        if df.empty:
            warnings.append(f"stock.db 查無 {tw_stock_id} 的技術面資料")
            return None

        row = df.iloc[0]
        # 核心欄缺（schema 根本不符）→ Fail Loud;核心欄 NaN → 視為無資料並告警（不填 0）。
        core = ["date", "stock_id", "close", "rsi", "upper_band", "lower_band"]
        missing = [c for c in core if c not in df.columns]
        if missing:
            raise DataSourceError(
                f"stock.db {self.stock_table} 缺核心欄 {missing}（schema 不符）"
            )
        if row[["close", "rsi", "upper_band", "lower_band"]].isna().any():
            warnings.append(f"stock.db {tw_stock_id} 最新列含 NaN，已跳過（不填 0）")
            return None

        def _opt(col: str) -> float | None:
            """盯盤卡加料欄（均線/KD/籌碼）：缺欄或 NaN → None（不捏造）。"""
            if col not in df.columns:
                return None
            v = row[col]
            return None if pd.isna(v) else float(v)

        return TechnicalSnapshot(
            stock_id=str(row["stock_id"]),
            as_of=str(row["date"]),
            close=float(row["close"]),
            rsi=float(row["rsi"]),
            upper_band=float(row["upper_band"]),
            lower_band=float(row["lower_band"]),
            ma20=_opt("ma20"),
            ma60=_opt("ma60"),
            kd_k=_opt("kd_k"),
            kd_d=_opt("kd_d"),
            foreign_net_lots=_opt("foreign_net_lots"),
            trust_net_lots=_opt("trust_net_lots"),
            total_net_lots=_opt("total_net_lots"),
        )

    def _fetch_us_link(
        self, us_stock_id: str, warnings: list[str]
    ) -> UsLinkSnapshot | None:
        if not us_stock_id:
            warnings.append("未提供連動美股代號，略過 fund.db 查詢")
            return None
        sql = (
            f"SELECT date, us_stock_id, close FROM {self.us_table} "
            "WHERE us_stock_id = ? ORDER BY date DESC LIMIT 1"
        )
        with _connect_readonly(self.fund_db) as conn:
            df = _read_sql(conn, sql, [us_stock_id])
        if df.empty:
            warnings.append(f"fund.db 查無 {us_stock_id} 的美股連動資料")
            return None

        row = df.iloc[0]
        if pd.isna(row["close"]):
            warnings.append(f"fund.db {us_stock_id} 最新收盤為 NaN，已跳過")
            return None
        return UsLinkSnapshot(
            us_stock_id=str(row["us_stock_id"]),
            as_of=str(row["date"]),
            close=float(row["close"]),
        )

    def _fetch_news(
        self,
        news_keywords: Sequence[str],
        as_of: date,
        lookback_days: int,
        warnings: list[str],
    ) -> list[NewsItem]:
        keywords = [k for k in (news_keywords or []) if k and k.strip()]
        if not keywords:
            warnings.append("未提供新聞關鍵字，略過 news.db 查詢")
            return []

        # ISO 日期字串可直接做字典序比較（等同時序比較）。
        cutoff = (as_of - timedelta(days=lookback_days)).isoformat()
        upper = as_of.isoformat()

        # 每個關鍵字對 title 與 content 各一個 LIKE，全部 OR 串接（參數化防注入）。
        like_clauses = " OR ".join(["title LIKE ? OR content LIKE ?"] * len(keywords))
        params: list = []
        for kw in keywords:
            pattern = f"%{kw}%"
            params.extend([pattern, pattern])
        params.extend([cutoff, upper])

        sql = (
            f"SELECT date, title, sentiment_score FROM {self.news_table} "
            f"WHERE ({like_clauses}) AND date >= ? AND date <= ? "
            "ORDER BY date DESC"
        )
        with _connect_readonly(self.news_db) as conn:
            df = _read_sql(conn, sql, params)

        if df.empty:
            warnings.append(
                f"news.db 於 [{cutoff}, {upper}] 內查無關鍵字 {keywords} 相關新聞"
            )
            return []

        items: list[NewsItem] = []
        clamped = 0
        skipped_nan = 0
        for _, r in df.iterrows():
            raw = r["sentiment_score"]
            if pd.isna(raw):
                skipped_nan += 1
                continue
            val = float(raw)
            capped = clamp(val, SENTIMENT_RAW_MIN, SENTIMENT_RAW_MAX)
            if not _almost_equal(capped, val):
                clamped += 1
            items.append(
                NewsItem(as_of=str(r["date"]), title=str(r["title"]), sentiment_score=capped)
            )
        if clamped:
            warnings.append(f"news.db 有 {clamped} 筆 sentiment_score 超出 [-1,1] 已 clamp")
        if skipped_nan:
            warnings.append(f"news.db 有 {skipped_nan} 筆 sentiment_score 為 NaN 已跳過")
        return items


def _almost_equal(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-12
