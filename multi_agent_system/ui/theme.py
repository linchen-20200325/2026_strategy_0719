"""theme.py — UI 配色（純常數，無 streamlit 依賴）。

數值鏡像自 my-stock-dashboard / my-Fund-dashboard 的 `shared/colors.py`（TRAFFIC_* 五色），
使本元件無縫融入既有 dashboard 的 GitHub-dark 主題。整合進特定 dashboard 時，
可傳入該 repo 的 `shared.colors` 值覆寫 `Palette`，達成單一真相源。

配色語意：交通號誌（綠=買進/放行、黃=中性、紅=賣出/停止），
**永遠**搭配 emoji + 中文字,不以顏色單獨表意（CVD/色盲友善）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    """五大行動 + 圖表配色。預設值鏡像既有 dashboard 的 Tailwind traffic-light。"""

    strong_buy: str = "#16a34a"   # 深綠（強買）
    add: str = "#22c55e"          # 綠（加碼）= TRAFFIC_GREEN
    hold: str = "#eab308"         # 黃（觀望）= TRAFFIC_YELLOW
    reduce: str = "#fb923c"       # 橘（減碼）= TRAFFIC_ORANGE
    strong_sell: str = "#ef4444"  # 紅（強賣）= TRAFFIC_RED
    neutral: str = "#888888"      # 灰（未知/abstain）= TRAFFIC_NEUTRAL
    bar: str = "#58a6ff"          # 圖表單一藍（GitHub accent），得分長條用


DEFAULT_PALETTE = Palette()
