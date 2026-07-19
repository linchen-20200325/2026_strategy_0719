"""test_watchlist_screener.py — 追蹤清單轉換 + 利多篩選/排序 + digest。"""

from __future__ import annotations

import pandas as pd

from multi_agent_system import CycleResult
from multi_agent_system.contracts import Action, DataPacket, FinalDecision
from multi_agent_system.pipeline import (
    WatchItem,
    bullish_ranked,
    format_bullish_digest,
    watchlist_from_df,
    watchlist_to_df,
)


def _cr(stock: str, action: Action, score: float | None, *, abstained: bool = False) -> CycleResult:
    decision = FinalDecision(stock, action, score, abstained, False, {}, "r")
    packet = DataPacket(stock, None, None, (), None, 0)
    return CycleResult(decision=decision, packet=packet)


# ------------------------------------------------------------ 追蹤清單轉換
def test_watchlist_df_roundtrip():
    items = [WatchItem("0050.TW", "", ("ETF", "台股50"), 0.20, 0.25, None, "ETF")]
    df = watchlist_to_df(items)
    assert list(df.columns) == ["類別", "代號", "連動美股/基金", "新聞關鍵字", "權重", "Sharpe"]
    back = watchlist_from_df(df)
    assert len(back) == 1
    assert back[0].tw_stock_id == "0050.TW"
    assert back[0].category == "ETF"
    assert back[0].keywords == ("ETF", "台股50")
    assert back[0].current_weight_ratio == 0.20


def test_watchlist_from_df_skips_empty_and_defaults():
    df = pd.DataFrame(
        {
            "類別": ["台股", ""],
            "代號": ["2330", ""],           # 第二列空代號 → 略過
            "連動美股/基金": ["NVDA", ""],
            "新聞關鍵字": ["台積電，半導體", ""],  # 全形逗號也吃
            "權重": [0.1, None],
            "Sharpe": [1.4, None],
        }
    )
    items = watchlist_from_df(df)
    assert len(items) == 1
    assert items[0].tw_stock_id == "2330"
    assert items[0].keywords == ("台積電", "半導體")


def test_watchlist_from_df_weight_nan_defaults():
    df = pd.DataFrame(
        {"類別": ["ETF"], "代號": ["0056.TW"], "連動美股/基金": [""],
         "新聞關鍵字": [""], "權重": [float("nan")], "Sharpe": [float("nan")]}
    )
    items = watchlist_from_df(df)
    assert items[0].current_weight_ratio == 0.10   # NaN → 安全預設
    assert items[0].sharpe is None


# ------------------------------------------------------------ 利多篩選
def test_bullish_ranked_filters_and_sorts():
    results = [
        _cr("A", Action.ADD, 0.65),
        _cr("B", Action.STRONG_BUY, 0.90),
        _cr("C", Action.HOLD, 0.45),            # 非利多
        _cr("D", Action.STRONG_SELL, 0.10),     # 非利多
        _cr("E", Action.ADD, None, abstained=True),  # abstain 排除
    ]
    ranked = bullish_ranked(results)
    assert [r.decision.tw_stock_id for r in ranked] == ["B", "A"]  # 由高到低,只留利多


def test_format_bullish_digest():
    txt = format_bullish_digest([_cr("A", Action.STRONG_BUY, 0.9), _cr("C", Action.HOLD, 0.4)])
    assert "利多" in txt and "A" in txt and "C" not in txt
    empty = format_bullish_digest([_cr("C", Action.HOLD, 0.4)])
    assert "無利多" in empty
