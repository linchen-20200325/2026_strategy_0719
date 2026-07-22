"""db.py — 共用 SQLite 唯讀連線 + 識別字白名單。L0/L1 infra（無 pandas，webhook/cron 皆可用）。

把散在 data_agent / macro_db / freshness 的「唯讀 URI 連線」+「SQL-injection 識別字白名單」
（表名/欄名無法參數化）收攏於一處 —— 尤其安全白名單原本兩份 identical copy（data_agent /
freshness），收一處避免其中一份被改而另一份漏改。
"""

from __future__ import annotations

import os
import re
import sqlite3


class DataSourceError(RuntimeError):
    """資料來源層級的致命錯誤（DB 缺檔 / 連線斷 / schema 不符）。"""


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_identifier(name: str, kind: str) -> str:
    """驗證資料表/欄名為安全識別字（無法參數化 → 防 SQL injection）。"""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"不合法的{kind}名稱：{name!r}（僅允許英數與底線）")
    return name


def connect_readonly(db_path: str) -> sqlite3.Connection:
    """以唯讀模式開啟 SQLite；檔案不存在時給出明確錯誤（Fail Loud）。"""
    if not os.path.exists(db_path):
        raise DataSourceError(f"資料庫檔不存在：{db_path}")
    try:
        # uri=True + mode=ro：保證不會對來源庫寫入。
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:  # 連線層錯誤
        raise DataSourceError(f"無法連線資料庫 {db_path}：{exc}") from exc
