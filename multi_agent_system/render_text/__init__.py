"""render_text — LINE / console 文字渲染層（把 L2 純資料攤成推播文字）。

與 `ui/`（Streamlit 元件）平行的「純文字」呈現層：compute / service 模組回傳純資料
（RunReport / CycleResult / LedgerReport / MacroReading …），本層以 duck-typing 讀屬性
排成 LINE / console 文字。**不 import 資料來源模組於載入期**（runner / market_digest /
ledger.report），資料由函式參數流入,避免載入期循環相依。

公開 API：format_* 系列（見 __all__）。
"""

from __future__ import annotations

from .ledger import format_equity, format_report

__all__ = [
    "format_report",
    "format_equity",
]
