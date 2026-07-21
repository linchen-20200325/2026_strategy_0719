"""test_financials_and_ai.py — ③ 財報讀取 + 新聞 AI 總結 + 盯盤卡渲染。

不打真 Gemini（monkeypatch）、不打真 DB（temp sqlite）。
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from multi_agent_system import ai_summary
from multi_agent_system.contracts import (
    Action,
    DataPacket,
    FinancialsSnapshot,
    NewsItem,
    TechnicalSnapshot,
)
from multi_agent_system.data_agent import DataAggregationAgent
from multi_agent_system.pipeline.runner import _fin_line, format_stock_card


# ------------------------------------------------------------------ 財報讀取
def _mk_stock_db(path, rows) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE stock_fundamentals (stock_id TEXT, roc_year INT, season INT, "
            "revenue REAL, gross_profit REAL, op_income REAL, net_income INT, eps REAL)"
        )
        conn.executemany(
            "INSERT INTO stock_fundamentals "
            "(stock_id,roc_year,season,revenue,gross_profit,op_income,net_income,eps) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )


def _agent(stock_db) -> DataAggregationAgent:
    return DataAggregationAgent(str(stock_db), "unused_fund.db", "unused_news.db")


def test_fetch_financials_latest_and_margins(tmp_path):
    p = tmp_path / "stock.db"
    _mk_stock_db(p, [
        ("2330", 114, 4, 1000.0, 500.0, 400.0, 300, 3.0),   # 舊
        ("2330", 115, 1, 2000.0, 1000.0, 800.0, 500, 5.0),  # 最新（roc_year/season 較大）
    ])
    fin = _agent(p)._fetch_financials("2330", [])
    assert (fin.roc_year, fin.season) == (115, 1)     # 取最新
    assert fin.period_label == "2026 Q1"
    assert fin.eps == 5.0
    assert fin.gross_margin_pct == 50.0               # 1000/2000
    assert fin.net_margin_pct == 25.0                 # 500/2000


def test_fetch_financials_zero_revenue_no_divzero(tmp_path):
    p = tmp_path / "stock.db"
    _mk_stock_db(p, [("2330", 115, 1, 0.0, 0.0, 0.0, 0, 0.0)])
    fin = _agent(p)._fetch_financials("2330", [])
    assert fin.gross_margin_pct is None               # revenue=0 → 不 ÷0
    assert fin.net_margin_pct is None


def test_fetch_financials_missing_table_returns_none(tmp_path):
    p = tmp_path / "stock.db"
    with sqlite3.connect(str(p)) as conn:
        conn.execute("CREATE TABLE other (x INT)")
    warns: list[str] = []
    assert _agent(p)._fetch_financials("2330", warns) is None
    assert warns                                      # 有告警（不靜默）


def test_fetch_financials_unknown_stock_none(tmp_path):
    p = tmp_path / "stock.db"
    _mk_stock_db(p, [("2330", 115, 1, 2000.0, 1000.0, 800.0, 500, 5.0)])
    assert _agent(p)._fetch_financials("9999", []) is None


# ------------------------------------------------------------------ _fin_line 渲染
def _fin(**kw):
    base = dict(stock_id="2330", roc_year=115, season=1, eps=5.0,
               revenue_k=1680982.0, gross_margin_pct=22.8, net_margin_pct=6.3)
    base.update(kw)
    return FinancialsSnapshot(**base)


def test_fin_line_units_thousand_to_yi():
    line = _fin_line(_fin())
    assert "2026 Q1季報" in line
    assert "EPS 5" in line
    assert "營收 16.8億" in line          # 1,680,982 千元 → 16.8 億（÷1e5）
    assert "毛利率 22.8%" in line and "淨利率 6.3%" in line


def test_fin_line_skips_missing_fields():
    line = _fin_line(_fin(eps=None, gross_margin_pct=None, net_margin_pct=None))
    assert "EPS" not in line and "毛利率" not in line
    assert "營收 16.8億" in line
    assert _fin_line(None) == ""


# ------------------------------------------------------------------ 盯盤卡整合
def _packet(**kw):
    base = dict(
        tw_stock_id="2330", technical=None, us_link=None, news=(),
        news_sentiment_mean=None, news_count=0, financials=None,
    )
    base.update(kw)
    return DataPacket(**base)


def _result(packet, action=Action.ADD, score=0.7):
    dec = SimpleNamespace(
        tw_stock_id=packet.tw_stock_id, action=action, final_score=score, abstained=False
    )
    return SimpleNamespace(decision=dec, packet=packet)


def _tech():
    return TechnicalSnapshot("2330", "2026-07-18", 927.0, 28.0, 1004.0, 924.0, ma20=955.0)


def test_card_uses_ai_summary_when_given():
    pkt = _packet(technical=_tech(), news=(NewsItem("2026-07-18", "台積電強", 0.6),),
                  news_count=1, financials=_fin())
    card = format_stock_card(_result(pkt), news_summary="AI：需求強、偏多。")
    assert "📰 AI：需求強、偏多。" in card
    assert "📈 2026 Q1季報" in card


def test_card_falls_back_to_headlines_without_ai():
    pkt = _packet(
        technical=_tech(),
        news=(NewsItem("2026-07-18", "頭條一", 0.6), NewsItem("2026-07-17", "頭條二", -0.2)),
        news_count=2,
    )
    card = format_stock_card(_result(pkt))          # 未給 news_summary
    assert "📰 頭條一；頭條二" in card


def test_card_no_news_no_news_line():
    pkt = _packet(technical=_tech())
    card = format_stock_card(_result(pkt))
    assert "📰" not in card


# ------------------------------------------------------------------ AI summary client
def _news(n):
    return [NewsItem("2026-07-18", f"新聞{i}", 0.3) for i in range(n)]


def test_summarize_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ai_summary.summarize_stock_news("2330", _news(3)) is None


def test_summarize_no_news_returns_none():
    assert ai_summary.summarize_stock_news("2330", [], api_key="k") is None


def test_summarize_happy_path(monkeypatch):
    captured = {}

    class _Resp:
        def __init__(self, data): self._data = data
        def read(self): return self._data
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = req.data
        payload = {"candidates": [{"content": {"parts": [{"text": "重點：需求強，偏多。"}]}}]}
        import json
        return _Resp(json.dumps(payload).encode())

    monkeypatch.setattr(ai_summary.urllib.request, "urlopen", fake_urlopen)
    out = ai_summary.summarize_stock_news("2330", _news(2), api_key="secret", model="gemini-2.5-flash")
    assert out == "重點：需求強，偏多。"
    assert "gemini-2.5-flash:generateContent" in captured["url"]
    assert "secret" in captured["url"]               # key 走 query string
    assert b"2330" in captured["body"]


def test_summarize_api_error_returns_none(monkeypatch):
    import urllib.error

    def boom(req, timeout=0):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(ai_summary.urllib.request, "urlopen", boom)
    assert ai_summary.summarize_stock_news("2330", _news(2), api_key="k") is None
