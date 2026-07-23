"""stock_store.py — 個股判讀 ledger 持久化（append-only JSONL）。L1 persist。

Phase 1（A · 個股 forward-test 止血）：把每檔 watchlist 的 FinalDecision 落帳，
否則當天所有個股訊號**永久遺失、不可回溯**。只存不可變判讀事實 + 判讀當下參考
收盤價（ref_close，順手自建個股價序列，供 Phase 2 對帳，無需動資料層）。
對帳結果不落地 —— 由 report 每次用累積 ref_close 序列重算（stateless，冪等）。

同 market ledger（store.py）模式：JSONL 一列一判讀、append 乾淨 diff、損毀列 raise
（Fail-Loud，§1，不靜默跳過）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from paths import STOCK_LEDGER_FILE as DEFAULT_STOCK_LEDGER_FILE  # 落地位置 SSOT（見 paths.py）

from ._jsonl import append_records, read_records, resolve_path


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
    is_simulated 判讀所依總經是否為模擬/注入值（非真實）。True → Phase 2 對帳應排除（§1）；舊列 → False
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
    is_simulated: bool = False


def _path(path: str | None) -> str:
    return resolve_path(path, "STOCK_LEDGER_FILE", DEFAULT_STOCK_LEDGER_FILE)


def append_stock_judgments(judgments: Iterable[StockJudgment], *, path: str | None = None) -> int:
    """append 一批個股判讀（一次 run 全 watchlist）。回實際寫入筆數。父目錄不存在則建立。"""
    return append_records(judgments, path=_path(path))


def read_stock_judgments(*, path: str | None = None) -> list[StockJudgment]:
    """讀全部個股判讀（升冪即寫入序）。檔不存在 → 空列。損毀列 → raise（Fail-Loud）。"""
    return read_records(StockJudgment, path=_path(path), label="stock_ledger")
