"""tests/conftest.py — 共用 fixtures：以臨時目錄建立三個 demo DB。"""

from __future__ import annotations

import pytest

from multi_agent_system import DataAggregationAgent
from scripts.seed_demo_dbs import seed_all


@pytest.fixture
def demo_paths(tmp_path):
    """在 pytest 臨時目錄建立乾淨的三庫（測試隔離，不碰 repo demo_data/）。"""
    return seed_all(str(tmp_path))


@pytest.fixture
def data_agent(demo_paths):
    return DataAggregationAgent(
        demo_paths["stock_db"], demo_paths["fund_db"], demo_paths["news_db"]
    )
