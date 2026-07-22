"""freshness.py — 資料新鮮度守門。

排程跑 AI 前先確認三個 DB 有「當期」資料;過期就大聲告警（Fail-Loud）,
避免 AI 跑在昨天/上週的舊資料上還一臉自信地出訊號（對照憲法 §2.4 Freshness）。

判定:latest_date(DB) 距離 as_of 的天數 > max_age_days → is_stale。
（max_age_days 預設 4,涵蓋週末 + 一天國定假日；週一早上看週五資料仍算新鮮。）
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from config import today_tw

from ..infra.db import connect_readonly, safe_identifier

# 預設 (資料庫路徑鍵, 資料表, 日期欄)。表名經白名單驗證防注入。
DEFAULT_TABLES: dict[str, tuple[str, str, str]] = {
    "stock": ("stock_db", "stock_technical", "date"),
    "fund": ("fund_db", "us_market", "date"),
    "news": ("news_db", "news", "date"),
}


@dataclass(frozen=True)
class DbFreshness:
    name: str
    latest_date: str | None
    age_days: int | None
    is_stale: bool
    error: str | None = None


@dataclass(frozen=True)
class FreshnessReport:
    as_of: str
    max_age_days: int
    items: tuple[DbFreshness, ...]

    @property
    def all_fresh(self) -> bool:
        return all(not i.is_stale and i.error is None for i in self.items)

    @property
    def stale_names(self) -> list[str]:
        return [i.name for i in self.items if i.is_stale or i.error]

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "max_age_days": self.max_age_days,
            "all_fresh": self.all_fresh,
            "items": [i.__dict__ for i in self.items],
        }


def _latest_date(db_path: str, table: str, col: str) -> str | None:
    safe_identifier(table, "資料表")
    safe_identifier(col, "欄位")
    with connect_readonly(db_path) as conn:
        row = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
    return row[0] if row and row[0] is not None else None


def check_freshness(
    paths: Mapping[str, str],
    *,
    tables: Mapping[str, tuple[str, str, str]] | None = None,
    as_of: date | None = None,
    max_age_days: int = 4,
) -> FreshnessReport:
    """檢查三個 DB 的最新資料日期;回傳逐庫報告（不 raise,由呼叫端決定嚴格與否）。"""
    as_of = as_of or today_tw()
    tables = tables or DEFAULT_TABLES
    items: list[DbFreshness] = []
    for name, (pkey, table, col) in tables.items():
        try:
            latest = _latest_date(paths[pkey], table, col)
            if latest is None:
                items.append(DbFreshness(name, None, None, True, "資料表為空"))
                continue
            age = (as_of - date.fromisoformat(str(latest)[:10])).days
            items.append(DbFreshness(name, str(latest), age, age > max_age_days))
        except Exception as exc:  # noqa: BLE001 - 單庫失敗轉為報告項,不拖垮其他庫
            items.append(DbFreshness(name, None, None, True, str(exc)))
    return FreshnessReport(as_of.isoformat(), max_age_days, tuple(items))
