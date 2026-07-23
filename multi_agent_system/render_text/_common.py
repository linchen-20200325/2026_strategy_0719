"""_common.py — render_text 共用文字 helper（跨 market / ledger / run_digest 的顯示原語）。

只放「純顯示」小工具：無 domain 型別依賴、無 I/O、無計算判斷。目前收價格顯示格式化。
"""

from __future__ import annotations


def _fmt_price(v: float) -> str:
    """價格顯示：四捨五入 2 位並去尾零（960.0→960、68.90→68.9）。"""
    return f"{round(float(v), 2):g}"
