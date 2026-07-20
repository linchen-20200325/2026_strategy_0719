"""run_pipeline.py — 排程 CLI 入口（早上 / 下午各跑一次）。

用法
----
    # 正式（三個 DB 路徑走環境變數 STOCK_DB / FUND_DB / NEWS_DB）
    python run_pipeline.py --session morning
    python run_pipeline.py --session afternoon --strict --output signals.json

    # 示範（自動 seed demo 資料庫,不需環境變數）
    python run_pipeline.py --session morning --demo

總經數據
--------
若設環境變數 MACRO_SPREAD_PCT + MACRO_CPI_YOY_PCT → 視為「真實注入值」(is_simulated=False)。
否則退回 SimulatedMacroProvider(中性情境) 並印警語(Fail-Loud:不把模擬值當實測)。

排程建議見 deploy/crontab.example 與 .github/workflows/run_pipeline.yml。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date

from multi_agent_system import (
    ConsoleNotifier,
    DataAggregationAgent,
    LinePusher,
    MockBrokerAPI,
    SimulatedMacroProvider,
    StaticMacroProvider,
    WorkflowOrchestrator,
)
from multi_agent_system.line_push import LinePushError
from multi_agent_system.macro_providers import MacroDataProvider
from multi_agent_system.pipeline import (
    DEMO_WATCHLIST,
    PipelineRunner,
    format_run_digest,
    load_db_paths,
    summarize,
)

logger = logging.getLogger("multi_agent_system.pipeline")


def _build_macro_provider() -> MacroDataProvider:
    """有環境變數 → 真實注入值;否則模擬情境並警告。"""
    spread = os.environ.get("MACRO_SPREAD_PCT")
    cpi = os.environ.get("MACRO_CPI_YOY_PCT")
    if spread is not None and cpi is not None:
        return StaticMacroProvider(
            yield_spread_pct=float(spread),
            cpi_yoy_pct=float(cpi),
            as_of=date.today().isoformat(),
            source="ENV",
            is_simulated=False,
        )
    logger.warning(
        "未設 MACRO_SPREAD_PCT / MACRO_CPI_YOY_PCT → 使用模擬中性總經情境"
        "（is_simulated=True）。接 FRED 後改注入真實值。"
    )
    return SimulatedMacroProvider(yield_spread_pct=1.0, cpi_yoy_pct=2.5, scenario="neutral")


def _resolve_db_paths(use_demo: bool) -> dict[str, str]:
    if use_demo:
        from scripts.seed_demo_dbs import default_demo_dir, seed_all

        return seed_all(default_demo_dir())
    return load_db_paths(allow_demo=True)


def _run_per_user(orchestrator: WorkflowOrchestrator, args) -> int:
    """個人化推播：每位訂閱者各自清單 → LINE push 逐人。"""
    from multi_agent_system.multiuser import run_per_user_push
    from multi_agent_system.subscribers import make_subscriber_store

    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not args.dry_run and not token:
        logger.error("個人化推播需 LINE_CHANNEL_ACCESS_TOKEN（或加 --dry-run 只預覽）")
        return 4

    # backend 依環境變數自動選：設了 GITHUB_TOKEN + GITHUB_REPO → 讀 repo 內共享 JSON;否則本機檔。
    results = run_per_user_push(
        make_subscriber_store(local_path=args.subscribers),
        orchestrator,
        _build_macro_provider(),
        channel_access_token=token,
        dry_run=args.dry_run,
    )
    pushed = sum(1 for r in results if r.pushed)
    logger.info("個人化推播：%d 訂閱者,%d 已推", len(results), pushed)
    for r in results:
        state = "✅ 推出" if r.pushed else ("dry-run" if args.dry_run else "略過")
        line = f"  {r.user_id}：追蹤 {r.n_tracked} / 利多 {r.n_bullish} → {state}"
        if r.error:
            line += f"（錯誤：{r.error}）"
        print(line)
    return 0


def _run_market_digest(orchestrator: WorkflowOrchestrator, args) -> int:
    """市場快訊（國際情勢 + 台股）→ broadcast 全體好友（同 mynews 主報告）。"""
    from config import INTL_NEWS_KEYWORDS, TW_MARKET_KEYWORDS
    from multi_agent_system.market_digest import (
        build_market_digest,
        summarize_news,
        tally_watchlist,
    )
    from multi_agent_system.pipeline import DEMO_WATCHLIST, build_request

    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not args.dry_run and not token:
        logger.error("市場快訊 broadcast 需 LINE_CHANNEL_ACCESS_TOKEN（或加 --dry-run 只預覽）")
        return 4

    macro = _build_macro_provider()
    as_of = date.today()
    results = orchestrator.run_batch(
        [build_request(it, macro, as_of=as_of) for it in DEMO_WATCHLIST]
    )
    agent = orchestrator.data_agent
    intl = summarize_news(agent.fetch_news(INTL_NEWS_KEYWORDS, as_of_date=as_of))
    tw = summarize_news(agent.fetch_news(TW_MARKET_KEYWORDS, as_of_date=as_of))
    digest = build_market_digest(
        session=args.session, day=as_of.strftime("%m/%d"),
        macro=macro.get_reading(), intl_news=intl, tw_news=tw,
        tally=tally_watchlist(results),
    )
    print(digest)
    if args.dry_run:
        return 0
    try:
        LinePusher(token, "broadcast").push_text(digest)
        logger.info("市場快訊已 broadcast 推播")
    except LinePushError as exc:
        logger.error("市場快訊推播失敗：%s", exc)
        return 4
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="多智能體投研排程執行")
    parser.add_argument("--session", required=True, choices=["morning", "afternoon"])
    parser.add_argument("--demo", action="store_true", help="使用自動 seed 的示範資料庫")
    parser.add_argument(
        "--strict", action="store_true", help="資料過期即中止（Fail-Loud）"
    )
    parser.add_argument("--auto-trade", action="store_true", help="送出 Mock 委託")
    parser.add_argument(
        "--line", action="store_true",
        help="推播一則 LINE 摘要（需環境變數 LINE_CHANNEL_ACCESS_TOKEN / LINE_TO）",
    )
    parser.add_argument("--output", help="把 JSON 報告寫到此檔")
    parser.add_argument("--max-age-days", type=int, default=4)
    parser.add_argument(
        "--per-user", action="store_true",
        help="改跑個人化推播：每位訂閱者各自清單 → LINE push 逐人",
    )
    parser.add_argument("--subscribers", default="subscribers.json", help="訂閱者 JSON 檔（--per-user 用）")
    parser.add_argument("--dry-run", action="store_true", help="--per-user / --market-digest 時只算不推")
    parser.add_argument(
        "--market-digest", action="store_true",
        help="改推『國際情勢+台股』市場快訊 → broadcast 全體好友（同 mynews 主報告）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        db_paths = _resolve_db_paths(args.demo)
    except OSError as exc:
        logger.error("%s", exc)
        return 2

    orchestrator = WorkflowOrchestrator(
        DataAggregationAgent(db_paths["stock_db"], db_paths["fund_db"], db_paths["news_db"]),
        broker=MockBrokerAPI(),
    )

    if args.per_user:
        return _run_per_user(orchestrator, args)

    if args.market_digest:
        return _run_market_digest(orchestrator, args)

    runner = PipelineRunner(
        orchestrator,
        DEMO_WATCHLIST,
        _build_macro_provider(),
        db_paths=db_paths,
        notifier=ConsoleNotifier(),
        max_age_days=args.max_age_days,
    )

    try:
        report = runner.run(
            args.session, strict_freshness=args.strict, auto_trade=args.auto_trade
        )
    except RuntimeError as exc:  # strict 新鮮度中止
        logger.error("%s", exc)
        return 3

    print(summarize(report))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, ensure_ascii=False, indent=2)
        logger.info("報告已寫入 %s", args.output)

    if args.line:
        pusher = LinePusher()
        if not pusher.is_configured:
            logger.error(
                "要求 --line 但未設 LINE_CHANNEL_ACCESS_TOKEN / LINE_TO,略過推播"
            )
            return 4
        try:
            pusher.push_text(format_run_digest(report))
            logger.info("已推播 LINE 摘要")
        except LinePushError as exc:
            logger.error("LINE 推播失敗：%s", exc)
            return 4

    return 0


if __name__ == "__main__":
    sys.exit(main())
