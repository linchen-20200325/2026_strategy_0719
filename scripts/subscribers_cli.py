"""subscribers_cli.py — 管理 LINE 訂閱者的追蹤清單（webhook 未接前先用此 CLI 填）。

用法：
    # 為某位 LINE user 新增/更新一檔追蹤（同代號視為更新）
    python scripts/subscribers_cli.py --store subscribers.json add U123 \\
        --tw 2330 --us NVDA --keywords 台積電,半導體 --category 台股

    python scripts/subscribers_cli.py --store subscribers.json list
    python scripts/subscribers_cli.py --store subscribers.json remove U123

之後接 LINE webhook / LIFF 時，改由入站服務呼叫 JsonSubscriberStore.add_item 即可,推播端不用改。
"""

from __future__ import annotations

import argparse
import sys

from config import DEFAULT_WEIGHT_RATIO
from multi_agent_system.pipeline import WatchItem
from multi_agent_system.subscribers import JsonSubscriberStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="管理 LINE 訂閱者追蹤清單")
    parser.add_argument("--store", required=True, help="訂閱 JSON 檔路徑")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增/更新某 user 的一檔追蹤")
    a.add_argument("user_id")
    a.add_argument("--tw", required=True, help="台股 / ETF 代號")
    a.add_argument("--us", default="", help="連動美股 / 基金代號")
    a.add_argument("--keywords", default="", help="新聞關鍵字（逗號分隔）")
    a.add_argument("--category", default="台股", choices=["台股", "ETF", "基金"])
    a.add_argument("--weight", type=float, default=DEFAULT_WEIGHT_RATIO)
    a.add_argument("--sharpe", type=float, default=None)

    sub.add_parser("list", help="列出所有訂閱者與其清單")

    r = sub.add_parser("remove", help="移除某 user")
    r.add_argument("user_id")

    args = parser.parse_args(argv)
    store = JsonSubscriberStore(args.store)

    if args.cmd == "add":
        keywords = tuple(
            k.strip() for k in args.keywords.replace("，", ",").split(",") if k.strip()
        )
        store.add_item(
            args.user_id,
            WatchItem(
                tw_stock_id=args.tw,
                us_stock_id=args.us,
                keywords=keywords,
                current_weight_ratio=args.weight,
                sharpe=args.sharpe,
                category=args.category,
            ),
        )
        print(f"✅ {args.user_id} 已加入/更新 {args.tw}（{args.category}）")

    elif args.cmd == "list":
        uids = store.user_ids()
        if not uids:
            print("（無訂閱者）")
            return 0
        for uid in uids:
            items = store.get(uid)
            print(f"👤 {uid}（{len(items)} 檔）")
            for it in items:
                us = f" ↔ {it.us_stock_id}" if it.us_stock_id else ""
                kw = f"〔{','.join(it.keywords)}〕" if it.keywords else ""
                print(f"   • {it.category} {it.tw_stock_id}{us} {kw}")

    elif args.cmd == "remove":
        store.remove_user(args.user_id)
        print(f"✅ 已移除 {args.user_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
