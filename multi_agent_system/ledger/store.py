"""store.py — 判讀 ledger 持久化（append-only JSONL）。L1 persist。

只存**不可變的判讀事實**（判了什麼、何時判）。對帳結果不落地 —— 由 report 每次用當前
market_index 重算（stateless，天然冪等，無 update-in-place、無 double-count）。

JSONL 選擇：一列一判讀、append 即乾淨 diff（git 友善、可正常 commit 累積歷史，
不像 force-push 會丟歷史）。損毀列 → raise（Fail-Loud，§1，不靜默跳過）。
"""

from __future__ import annotations

from dataclasses import dataclass

from config import REGIME_UNTAGGED
from paths import LEDGER_FILE as DEFAULT_LEDGER_FILE  # 落地位置 SSOT（見 paths.py）

from ._jsonl import append_records, read_records, resolve_path


@dataclass(frozen=True)
class Judgment:
    """一筆大盤判讀的存檔事實（不可變）。

    judged_at   判讀當下（ISO tz-aware，台灣時間）
    judged_date 判讀歸屬日 YYYY-MM-DD（台灣日期，對帳進場對齊用）
    session     morning / afternoon（決定進場 = 當日 open 還是次一交易日 open）
    label       偏多 / 中性 / 偏空（走 config REGIME_LABEL_* SSOT）
    overall     綜合偏多度 ∈ [0,1]
    regime      判讀當下市場 regime（殖利率曲線：倒掛/正常）；舊列無此欄 → 未標記
    is_simulated 判讀所依總經是否為**模擬/注入值**（非真實 fund.db/API）。True → 對帳時
                 **排除、不計入成績**（§1：模擬值不可當實測污染 track record）；舊列 → False
    """

    judged_at: str
    judged_date: str
    session: str
    label: str
    overall: float
    regime: str = REGIME_UNTAGGED
    is_simulated: bool = False


def _path(path: str | None) -> str:
    return resolve_path(path, "LEDGER_FILE", DEFAULT_LEDGER_FILE)


def append_judgment(j: Judgment, *, path: str | None = None) -> None:
    """append 一列判讀。父目錄不存在則建立。"""
    append_records([j], path=_path(path))


def read_judgments(*, path: str | None = None) -> list[Judgment]:
    """讀全部判讀（升冪即寫入序）。檔不存在 → 空列。損毀列 → raise（Fail-Loud）。"""
    return read_records(Judgment, path=_path(path))
