"""test_pipeline.py — 新鮮度守門、watchlist、排程 runner、CLI 冒煙。"""

from __future__ import annotations

from datetime import date

import pytest

from multi_agent_system import (
    DataAggregationAgent,
    MockBrokerAPI,
    SimulatedMacroProvider,
    WorkflowOrchestrator,
)
from multi_agent_system.pipeline import (
    DEMO_WATCHLIST,
    PipelineRunner,
    WatchItem,
    check_freshness,
    load_db_paths,
    summarize,
)

AS_OF = date(2026, 7, 19)   # demo 最新資料為 2026-07-18


# ------------------------------------------------------------------ freshness
def test_freshness_fresh(demo_paths):
    rep = check_freshness(demo_paths, as_of=AS_OF, max_age_days=4)
    assert rep.all_fresh
    assert rep.stale_names == []
    stock = next(i for i in rep.items if i.name == "stock")
    assert stock.latest_date == "2026-07-18"
    assert stock.age_days == 1


def test_freshness_stale_when_old(demo_paths):
    rep = check_freshness(demo_paths, as_of=date(2026, 8, 1), max_age_days=4)
    assert not rep.all_fresh
    assert set(rep.stale_names) == {"stock", "fund", "news"}


def test_freshness_missing_db_is_error_item(demo_paths, tmp_path):
    paths = dict(demo_paths)
    paths["news_db"] = str(tmp_path / "does_not_exist.db")
    rep = check_freshness(paths, as_of=AS_OF)
    news = next(i for i in rep.items if i.name == "news")
    assert news.is_stale and news.error is not None
    assert not rep.all_fresh


def test_freshness_report_serializable(demo_paths):
    rep = check_freshness(demo_paths, as_of=AS_OF)
    import json

    json.dumps(rep.to_dict(), ensure_ascii=False)


# ------------------------------------------------------------------ watchlist
def test_watchitem_portfolio_state():
    it = WatchItem("2330", "NVDA", ("台積電",), 0.10, 0.20, 1.4)
    ps = it.portfolio_state()
    assert ps.current_weight_ratio == 0.10
    assert ps.max_weight_ratio == 0.20
    assert ps.sharpe == 1.4


def test_load_db_paths_missing_env_raises(monkeypatch):
    for k in ("STOCK_DB", "FUND_DB", "NEWS_DB"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(EnvironmentError):
        load_db_paths()


def test_load_db_paths_from_env(monkeypatch):
    monkeypatch.setenv("STOCK_DB", "/x/stock.db")
    monkeypatch.setenv("FUND_DB", "/x/fund.db")
    monkeypatch.setenv("NEWS_DB", "/x/news.db")
    paths = load_db_paths()
    assert paths["stock_db"] == "/x/stock.db"


# ------------------------------------------------------------------ runner
def _runner(demo_paths, notifier=None):
    agent = DataAggregationAgent(
        demo_paths["stock_db"], demo_paths["fund_db"], demo_paths["news_db"]
    )
    orch = WorkflowOrchestrator(agent, broker=MockBrokerAPI())
    macro = SimulatedMacroProvider(yield_spread_pct=1.0, cpi_yoy_pct=2.5)
    return PipelineRunner(
        orch, DEMO_WATCHLIST, macro, db_paths=demo_paths, notifier=notifier
    )


def test_runner_runs_watchlist(demo_paths):
    report = _runner(demo_paths).run("morning", as_of=AS_OF)
    assert report.session == "morning"
    assert len(report.results) == len(DEMO_WATCHLIST)
    # 可序列化
    import json

    json.dumps(report.to_dict(), ensure_ascii=False)
    assert isinstance(summarize(report), str)


def test_runner_notifies_actionable_only(demo_paths):
    captured = []

    class FakeNotifier:
        def notify(self, decision):
            captured.append(decision.tw_stock_id)

    report = _runner(demo_paths, notifier=FakeNotifier()).run("afternoon", as_of=AS_OF)
    actionable_ids = [r.decision.tw_stock_id for r in report.actionable()]
    # 通知次數 == 可行動訊號數（Hold/abstain 不推）
    assert captured == actionable_ids


def test_runner_strict_freshness_raises_when_stale(demo_paths):
    with pytest.raises(RuntimeError):
        _runner(demo_paths).run("morning", as_of=date(2026, 9, 1), strict_freshness=True)


def test_runner_invalid_session_raises(demo_paths):
    with pytest.raises(ValueError):
        _runner(demo_paths).run("evening", as_of=AS_OF)


# ------------------------------------------------------------------ CLI smoke
def test_cli_demo_smoke():
    import run_pipeline

    rc = run_pipeline.main(["--session", "morning", "--demo"])
    assert rc == 0
