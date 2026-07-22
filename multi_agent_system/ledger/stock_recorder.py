"""stock_recorder.py — 把個股判讀寫進 stock_ledger（record 階段）。L2。

pipeline broadcast 完後呼叫，將 run_batch 每檔 CycleResult 存成一筆 StockJudgment。
**record 失敗不可擋推播**（§8.1 #5 失敗降級）：loud log + 回已寫筆數（可為 0），不 raise。
**無 lookahead**：ref_close 取判讀當下技術快照收盤（判讀時已知的過去值），非未來價。

以 duck-typing 收 results（各 item 需有 `.decision`（FinalDecision）+ `.packet`（DataPacket）），
不 import integration_agent，避免 ledger → 上層的耦合。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime

from config import now_tw

from .stock_store import StockJudgment, append_stock_judgments

logger = logging.getLogger(__name__)


def record_stock_judgments(
    results: Iterable,
    *,
    session: str,
    is_simulated: bool = False,
    when: datetime | None = None,
    path: str | None = None,
) -> int:
    """把一批 CycleResult 的個股判讀落帳。回實際寫入筆數；失敗 loud log + 回 0（不擋推播）。

    results：orchestrator.run_batch(...) 輸出（各帶 decision + packet）。
    ref_close 取 packet.technical.close（缺技術面 → None，仍記判讀，不捏價，§1）。
    is_simulated：總經是否為模擬值（reading.is_simulated）。True → Phase 2 對帳應排除（§1）。
    """
    try:
        stamp = when or now_tw()
        judged_at = stamp.isoformat()
        judged_date = stamp.date().isoformat()
        js: list[StockJudgment] = []
        for r in results:
            d = r.decision
            tech = r.packet.technical
            js.append(
                StockJudgment(
                    judged_at=judged_at,
                    judged_date=judged_date,
                    session=session,
                    stock_id=d.tw_stock_id,
                    action=d.action.name,
                    final_score=(None if d.final_score is None else round(float(d.final_score), 6)),
                    abstained=bool(d.abstained),
                    ref_close=(float(tech.close) if tech is not None else None),
                    ref_as_of=(tech.as_of if tech is not None else None),
                    is_simulated=bool(is_simulated),
                )
            )
        n = append_stock_judgments(js, path=path)
        logger.info("stock_ledger 已記錄 %d 檔個股判讀（%s %s）", n, judged_date, session)
        return n
    except Exception as exc:  # noqa: BLE001 — record 為次要，失敗須 loud 但絕不擋推播（§8.1#5）
        logger.error("stock_ledger 記錄失敗（不擋推播）：%s", exc)
        return 0
