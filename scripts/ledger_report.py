"""ledger_report.py — 判讀 forward-test 對帳報表（讀 stock.db market_index + ledger.jsonl）。

用法:
    STOCK_DB=/path/stock.db LEDGER_FILE=/path/ledger.jsonl python scripts/ledger_report.py [--line]
    python scripts/ledger_report.py --stock-db stock.db --ledger ledger.jsonl [--line]

對帳為 **stateless**:每次用當前 market_index 對全部判讀重算命中率（冪等）。
--line：設了 LINE_CHANNEL_ACCESS_TOKEN 時 broadcast 報表到全體好友;否則只印。
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import date

# scripts/ 執行時把 repo root 掛上 path（對照 subscribers_cli 需 PYTHONPATH 的教訓，這裡自帶）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_agent_system.ledger import (  # noqa: E402
    PriceBar,
    build_report,
    format_report,
    read_judgments,
)

logger = logging.getLogger("ledger_report")


def _read_market_index_bars(stock_db: str) -> list[PriceBar]:
    """讀 stock.db market_index → 升冪 (date, open) 交易日序列。缺檔/空表 → raise（Fail-Loud）。"""
    if not os.path.exists(stock_db):
        raise FileNotFoundError(f"stock.db 不存在：{stock_db}")
    con = sqlite3.connect(stock_db)
    try:
        rows = con.execute(
            "SELECT date, open FROM market_index WHERE open IS NOT NULL ORDER BY date"
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise ValueError(f"{stock_db} market_index 無資料（無法對帳）")
    return [PriceBar(date.fromisoformat(str(d)), float(o)) for d, o in rows]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="判讀 forward-test 對帳報表")
    ap.add_argument("--stock-db", default=os.environ.get("STOCK_DB", "stock.db"))
    ap.add_argument("--ledger", default=os.environ.get("LEDGER_FILE", "ledger.jsonl"))
    ap.add_argument("--line", action="store_true",
                    help="broadcast 報表到 LINE（需 LINE_CHANNEL_ACCESS_TOKEN）")
    args = ap.parse_args(argv)

    judgments = read_judgments(path=args.ledger)
    if not judgments:
        logger.warning("ledger 無判讀記錄（%s）→ 尚無可對帳資料", args.ledger)
        print("📒 判讀對帳：尚無判讀記錄（等 broadcast 開始累積）")
        return 0

    bars = _read_market_index_bars(args.stock_db)
    text = format_report(build_report(judgments, bars))
    print(text)

    if args.line:
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
        if not token:
            logger.error("--line 需 LINE_CHANNEL_ACCESS_TOKEN")
            return 4
        from multi_agent_system import LinePusher
        from multi_agent_system.line_push import LinePushError
        try:
            LinePusher(token, "broadcast").push_text(text)
            logger.info("對帳報表已 broadcast 推播")
        except LinePushError as exc:
            logger.error("對帳報表推播失敗：%s", exc)
            return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
