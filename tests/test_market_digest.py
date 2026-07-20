"""test_market_digest.py — 市場快訊（國際情勢 + 台股，broadcast）純函式 + CLI 冒煙。"""

from __future__ import annotations

from types import SimpleNamespace

from multi_agent_system.contracts import Action, MacroReading, NewsItem
from multi_agent_system.market_digest import (
    NewsStat,
    build_market_digest,
    sentiment_label,
    summarize_news,
    tally_watchlist,
)


def _news(title, s):
    return NewsItem(as_of="2026-07-18", title=title, sentiment_score=s)


def _res(action, abstained=False, code="X"):
    return SimpleNamespace(
        decision=SimpleNamespace(action=action, abstained=abstained, tw_stock_id=code)
    )


# ---------------------------------------------------------------- sentiment_label
def test_sentiment_label_bands():
    assert sentiment_label(0.5) == "偏多"
    assert sentiment_label(-0.5) == "偏空"
    assert sentiment_label(0.0) == "中性"
    assert sentiment_label(None) == "無資料"


# ---------------------------------------------------------------- summarize_news
def test_summarize_news_empty():
    st = summarize_news([])
    assert st.count == 0
    assert st.mean is None
    assert st.top_titles == []


def test_summarize_news_mean_and_top():
    items = [_news("小利多", 0.1), _news("大利空", -0.9), _news("中性", 0.0)]
    st = summarize_news(items, top_n=2)
    assert st.count == 3
    assert abs(st.mean - (0.1 - 0.9 + 0.0) / 3) < 1e-9
    assert st.top_titles[0] == "大利空"          # 依 |sentiment| 由大到小
    assert len(st.top_titles) == 2


# ---------------------------------------------------------------- tally_watchlist
def test_tally_watchlist_counts():
    results = [
        _res(Action.STRONG_BUY, code="2330"),
        _res(Action.ADD, code="2454"),
        _res(Action.HOLD, abstained=True),
        _res(Action.REDUCE),
        _res(Action.STRONG_SELL),
    ]
    t = tally_watchlist(results)
    assert (t.n, t.bullish, t.hold, t.bearish) == (5, 2, 1, 2)
    assert t.bullish_names == ["2330", "2454"]


def test_tally_bullish_but_abstained_is_hold():
    # 棄權即使 action 標多也算觀望（資料不足，不列利多）
    t = tally_watchlist([_res(Action.STRONG_BUY, abstained=True, code="9999")])
    assert (t.bullish, t.hold) == (0, 1)
    assert t.bullish_names == []


# ---------------------------------------------------------------- build_market_digest
def _macro(spread, cpi, *, simulated=False):
    return MacroReading(
        yield_spread_pct=spread, cpi_yoy_pct=cpi,
        source="test", as_of="2026-07-18", is_simulated=simulated,
    )


def test_build_digest_sections_and_flags():
    digest = build_market_digest(
        session="afternoon", day="07/19",
        macro=_macro(-0.2, 5.5),
        intl_news=summarize_news([_news("Fed 升息", -0.4)]),
        tw_news=summarize_news([_news("台積電強", 0.6)]),
        tally=tally_watchlist([_res(Action.STRONG_BUY, code="2330")]),
    )
    assert "市場快訊" in digest and "收盤後" in digest
    assert "國際情勢" in digest and "台股" in digest
    assert "⚠️倒掛" in digest and "🔥偏熱" in digest      # spread<=0 且 CPI>=5
    assert "2330" in digest


def test_build_digest_simulated_flag_and_no_data():
    digest = build_market_digest(
        session="morning", day="07/20",
        macro=_macro(1.2, 2.1, simulated=True),          # 正常 + 溫和 + 模擬
        intl_news=NewsStat(0, None, []),                 # 無外電
        tw_news=summarize_news([_news("台股紅", 0.3)]),
        tally=tally_watchlist([_res(Action.HOLD)]),
    )
    assert "（模擬）" in digest
    assert "正常" in digest and "溫和" in digest
    assert "無資料" in digest                            # 國際新聞 Fail-Loud 誠實


# ---------------------------------------------------------------- CLI 冒煙
def test_cli_market_digest_dry_run():
    import run_pipeline

    rc = run_pipeline.main(["--session", "afternoon", "--demo", "--market-digest", "--dry-run"])
    assert rc == 0
