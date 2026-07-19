"""test_data_agent.py — 跨庫查詢、缺料告警、新聞視窗、防注入。"""

from __future__ import annotations

from datetime import date

import pytest

from multi_agent_system import DataAggregationAgent
from multi_agent_system.data_agent import DataSourceError

AS_OF = date(2026, 7, 19)


def test_fetch_latest_technical(data_agent):
    packet = data_agent.aggregate("2330", "NVDA", ["台積電"], as_of_date=AS_OF)
    assert packet.has_technical
    # 應取最新一期 (2026-07-18)
    assert packet.technical.as_of == "2026-07-18"
    assert packet.technical.rsi == pytest.approx(28.0)
    assert packet.us_link is not None
    assert packet.us_link.us_stock_id == "NVDA"


def test_news_window_and_sentiment(data_agent):
    packet = data_agent.aggregate("2330", "NVDA", ["台積電", "半導體", "TSMC"], as_of_date=AS_OF)
    # 舊聞 (07-10) 超出 7 天視窗，不應計入
    assert all(n.as_of >= "2026-07-12" for n in packet.news)
    assert packet.news_count == 3
    assert packet.news_sentiment_mean == pytest.approx((0.72 + 0.55 - 0.30) / 3)


def test_missing_stock_returns_none_with_warning(data_agent):
    packet = data_agent.aggregate("9999", "NVDA", ["台積電"], as_of_date=AS_OF)
    assert packet.technical is None
    assert any("9999" in w for w in packet.warnings)


def test_no_keywords_skips_news(data_agent):
    packet = data_agent.aggregate("2330", "NVDA", [], as_of_date=AS_OF)
    assert packet.news_count == 0
    assert packet.news_sentiment_mean is None
    assert any("關鍵字" in w for w in packet.warnings)


def test_missing_db_raises(tmp_path):
    agent = DataAggregationAgent(
        str(tmp_path / "nope_stock.db"),
        str(tmp_path / "nope_fund.db"),
        str(tmp_path / "nope_news.db"),
    )
    with pytest.raises(DataSourceError):
        agent.aggregate("2330", "NVDA", ["台積電"], as_of_date=AS_OF)


def test_keyword_injection_is_safe(data_agent):
    # 惡意字串應被當作 LIKE 值處理，不會破壞 SQL；查無資料即可（不炸）。
    packet = data_agent.aggregate(
        "2330", "NVDA", ["'; DROP TABLE news;--"], as_of_date=AS_OF
    )
    assert packet.news_count == 0


def test_bad_table_name_rejected(demo_paths):
    with pytest.raises(ValueError):
        DataAggregationAgent(
            demo_paths["stock_db"],
            demo_paths["fund_db"],
            demo_paths["news_db"],
            news_table="news; DROP TABLE x",
        )


def test_to_json_dict_serializable(data_agent):
    import json

    packet = data_agent.aggregate("2330", "NVDA", ["台積電"], as_of_date=AS_OF)
    js = packet.to_json_dict()
    # 應可被 json 序列化（datetime 已轉字串）
    json.dumps(js, ensure_ascii=False)
    assert js["tw_stock_id"] == "2330"
