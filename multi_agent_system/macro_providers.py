"""macro_providers.py — 總經數據來源介面 + 模擬/注入實作。

為什麼要有這一層？
------------------
使用者需求要「模擬接入美債利差與 CPI」。但直接在 agent 裡寫死一組數字，
等於違反 Fail-Loud / Never-Fake 原則（把假資料當真資料）。

解法：把「資料來源」抽象成 provider 介面，並讓每一筆輸出都帶
`is_simulated` 旗標與 `source` 血緣：
* `StaticMacroProvider`  — 呼叫端注入「真實」數值（例如已從 FRED 抓好），is_simulated=False。
* `SimulatedMacroProvider` — 明確標示為模擬情境（Demo / 壓力測試），is_simulated=True。
* `FredMacroProvider`     — 真實 FRED 介接的骨架，尚未接線時 raise（不回假值）。

如此既滿足「可模擬跑通」，又不欺騙下游把模擬值誤當實測值。
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from .contracts import MacroReading


class MacroDataProvider(Protocol):
    """總經數據 provider 契約。"""

    def get_reading(self) -> MacroReading: ...


class StaticMacroProvider:
    """呼叫端注入現成（真實）數值。

    典型用法：上游已從 FRED（DGS10, DGS2, CPIAUCSL 年增率）抓好，於此注入。
    """

    def __init__(
        self,
        *,
        yield_spread_pct: float,
        cpi_yoy_pct: float,
        as_of: str,
        source: str = "injected",
        is_simulated: bool = False,
    ) -> None:
        self._reading = MacroReading(
            yield_spread_pct=yield_spread_pct,
            cpi_yoy_pct=cpi_yoy_pct,
            source=source,
            as_of=as_of,
            is_simulated=is_simulated,
        )

    def get_reading(self) -> MacroReading:
        return self._reading


class SimulatedMacroProvider(StaticMacroProvider):
    """明確的「模擬情境」provider（Demo / 情境壓測用）。

    所有輸出強制 is_simulated=True、source 標記 'SIMULATED'，
    下游因此能在報告中標註「此為模擬總經情境，非實測」。
    """

    def __init__(
        self,
        *,
        yield_spread_pct: float,
        cpi_yoy_pct: float,
        as_of: str | None = None,
        scenario: str = "baseline",
    ) -> None:
        super().__init__(
            yield_spread_pct=yield_spread_pct,
            cpi_yoy_pct=cpi_yoy_pct,
            as_of=as_of or date.today().isoformat(),
            source=f"SIMULATED:{scenario}",
            is_simulated=True,
        )


class FredMacroProvider:
    """真實 FRED 介接骨架（10Y-2Y 利差 + CPI 年增率）。

    數學/來源
    ---------
        yield_spread_pct = DGS10 - DGS2           (FRED daily, 單位 %)
        cpi_yoy_pct      = (CPIAUCSL_t / CPIAUCSL_{t-12} - 1) * 100

    尚未接線時 **raise NotImplementedError**（Fail Loud），
    絕不回傳預設/假數字讓系統「看起來成功」。
    未來對接時，於此實作 HTTP 抓取 + release_date 對齊（防 lookahead）。
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def get_reading(self) -> MacroReading:  # pragma: no cover - 尚未接線
        raise NotImplementedError(
            "FredMacroProvider 尚未接線。請改用 StaticMacroProvider 注入真實值，"
            "或 SimulatedMacroProvider 明確跑模擬情境。"
        )
