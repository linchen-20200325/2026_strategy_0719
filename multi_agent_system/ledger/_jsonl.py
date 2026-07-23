"""_jsonl.py — ledger 共用 append-only JSONL I/O。L1 persist。

store.py（大盤判讀）與 stock_store.py（個股判讀）落地 I/O ~90% 相同 → 收攏於此：
落地路徑解析（顯式 > env > SSOT）、父目錄自建、一列一 dataclass、壞列 raise（Fail-Loud，§1）。
各 store 只保留自身 dataclass + 預設路徑，I/O 委派本檔（修 corrupt-line 契約一處即全收）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict


def resolve_path(explicit: str | None, env_var: str, default: str) -> str:
    """落地路徑：顯式 path > 環境變數 > SSOT 預設。"""
    return explicit or os.environ.get(env_var) or default


def append_records(records: Iterable, *, path: str) -> int:
    """append 一批 dataclass 列（一列一筆，json.dumps(asdict)）。回實際寫入筆數。

    空輸入 → 回 0 且**不建檔**；否則父目錄不存在則建立。
    """
    recs = list(records)
    if not recs:
        return 0
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    return len(recs)


def read_records(cls, *, path: str, label: str = "ledger") -> list:
    """讀全部列 → cls(**each)。檔不存在 → 空列。壞列 → raise ValueError（Fail-Loud，§1）。"""
    if not os.path.exists(path):
        return []
    out: list = []
    with open(path, encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(cls(**json.loads(line)))
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"{path}:{line_no} {label} 解析失敗：{exc}") from exc
    return out
