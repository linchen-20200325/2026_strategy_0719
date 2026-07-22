"""stock_store.py — 個股判讀 ledger 持久化（append-only JSONL）。L1 persist。

Phase 1（A · 個股 forward-test 止血）：把每檔 watchlist 的 FinalDecision 落帳，
否則當天所有個股訊號**永久遺失、不可回溯**。只存不可變判讀事實 + 判讀當下參考
收盤價（ref_close，順手自建個股價序列，供 Phase 2 對帳，無需動資料層）。
對帳結果不落地 —— 由 report 每次用累積 ref_close 序列重算（stateless，冪等）。

同 market ledger（store.py）模式：JSONL 一列一判讀、append 乾淨 diff、損毀列 raise
（Fail-Loud，§1，不靜默跳過）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass

DEFAULT_STOCK_LEDGER_FILE = "stock_ledger.jsonl"


@dataclass(frozen=True)
class StockJudgment:
    """一檔個股單次判讀的存檔事實（不可變）。

    judged_at    判讀當下（ISO tz-aware，台灣時間）
    judged_date  判讀歸屬日 YYYY-MM-DD（台灣日期，對帳進場對齊用）
    session      morning / afternoon
    stock_id     台股代號
    action       Action.name（STRONG_BUY / ADD / HOLD / REDUCE / STRONG_SELL）
    final_score  綜合分數 ∈ [0,1]；abstain（缺必要專家）→ None（不臆造）
    abstained    是否棄權（資料不足未下判讀 → Phase 2 對帳應排除）
    ref_close    判讀當下該股參考收盤（元；自建價序列 + 對帳 entry 參考）；缺技術面 → None
    ref_as_of    ref_close 歸屬交易日 YYYY-MM-DD（技術快照 as_of，非抓取日）；缺 → None
    """

    judged_at: str
    judged_date: str
    session: str
    stock_id: str
    action: str
    final_score: float | None
    abstained: bool
    ref_close: float | None
    ref_as_of: str | None


def _path(path: str | None) -> str:
    return path or os.environ.get("STOCK_LEDGER_FILE") or DEFAULT_STOCK_LEDGER_FILE


def append_stock_judgments(judgments: Iterable[StockJudgment], *, path: str | None = None) -> int:
    """append 一批個股判讀（一次 run 全 watchlist）。回實際寫入筆數。父目錄不存在則建立。"""
    js = list(judgments)
    if not js:
        return 0
    p = _path(path)
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for j in js:
            f.write(json.dumps(asdict(j), ensure_ascii=False) + "\n")
    return len(js)


def read_stock_judgments(*, path: str | None = None) -> list[StockJudgment]:
    """讀全部個股判讀（升冪即寫入序）。檔不存在 → 空列。損毀列 → raise（Fail-Loud）。"""
    p = _path(path)
    if not os.path.exists(p):
        return []
    out: list[StockJudgment] = []
    with open(p, encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(StockJudgment(**json.loads(line)))
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"{p}:{line_no} stock_ledger 解析失敗：{exc}") from exc
    return out
