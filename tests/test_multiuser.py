"""test_multiuser.py — 個人化推播（push 逐人 + userId→清單 store）。

驗證 run_per_user_push：每位訂閱者跑「自己的」清單，只有有利多的人才被推，
且走 LINE push（單一對象 = 該 userId），不是 multicast。
"""

from __future__ import annotations

from datetime import date

import pytest

from multi_agent_system import (
    DataAggregationAgent,
    MockBrokerAPI,
    SimulatedMacroProvider,
    WorkflowOrchestrator,
)
from multi_agent_system.line_push import LinePusher, LinePushError
from multi_agent_system.multiuser import run_per_user_push
from multi_agent_system.pipeline import WatchItem

AS_OF = date(2026, 7, 19)   # demo 最新資料為 2026-07-18


def _orch(demo_paths):
    agent = DataAggregationAgent(
        demo_paths["stock_db"], demo_paths["fund_db"], demo_paths["news_db"]
    )
    return WorkflowOrchestrator(agent, broker=MockBrokerAPI())


def _macro():
    return SimulatedMacroProvider(yield_spread_pct=1.0, cpi_yoy_pct=2.5)


def _item(code, us="", cat="台股"):
    kw = ("半導體",) if code != "9999" else ()
    return WatchItem(code, us, kw, 0.10, 0.20, 1.4, cat)


class _MemStore:
    """記憶體版 store（測試用，實作 _Store 的 user_ids/get）。"""

    def __init__(self, data):
        self._data = data

    def user_ids(self):
        return list(self._data.keys())

    def get(self, uid):
        return list(self._data.get(uid, []))


@pytest.fixture
def captured_push(monkeypatch):
    """攔截 LinePusher.push_text，記錄 (to, text) 而不真的打 API。"""
    sent = []

    def fake_push(self, text):
        sent.append((self.to, text))

    monkeypatch.setattr(LinePusher, "push_text", fake_push)
    return sent


# ---------------------------------------------------------------- 逐人推播
def test_push_only_to_users_with_bullish(demo_paths, captured_push):
    # U1 追 2330（demo 為 STRONG_BUY）→ 應被推；U2 追 9999（查無 → abstain）→ 不推
    store = _MemStore({"U1": [_item("2330", "NVDA")], "U2": [_item("9999")]})
    results = run_per_user_push(
        store, _orch(demo_paths), _macro(),
        channel_access_token="TOKEN", as_of=AS_OF,
    )
    by_uid = {r.user_id: r for r in results}

    assert by_uid["U1"].pushed is True
    assert by_uid["U1"].n_bullish == 1
    assert by_uid["U2"].pushed is False
    assert by_uid["U2"].n_bullish == 0

    # 只推給 U1，且是 push 到「該 userId 本人」（不是 multicast 名單）
    assert len(captured_push) == 1
    to, text = captured_push[0]
    assert to == "U1"
    assert "2330" in text


def test_push_per_user_uses_own_watchlist(demo_paths, captured_push):
    # 兩人都有利多標的（各自 2330）→ 兩則各推各的對象
    store = _MemStore({"U1": [_item("2330")], "U2": [_item("2330")]})
    run_per_user_push(
        store, _orch(demo_paths), _macro(),
        channel_access_token="TOKEN", as_of=AS_OF,
    )
    assert sorted(to for to, _ in captured_push) == ["U1", "U2"]


def test_only_bullish_false_pushes_everyone(demo_paths, captured_push):
    # only_bullish=False → 即使無利多也推（誠實顯示「目前無利多訊號」）
    store = _MemStore({"U2": [_item("9999")]})
    results = run_per_user_push(
        store, _orch(demo_paths), _macro(),
        channel_access_token="TOKEN", as_of=AS_OF, only_bullish=False,
    )
    assert results[0].pushed is True
    assert len(captured_push) == 1
    assert captured_push[0][0] == "U2"


def test_full_watch_pushes_all_with_cards(demo_paths, captured_push):
    # full_watch=True → 全清單盯盤：追 9999(查無→abstain) 的人也收到（誠實顯示資料不足），
    # 卡片含技術/籌碼欄位與盯盤頁尾（對齊 LINE 盯盤 bot 體驗）。
    store = _MemStore({"U1": [_item("2330", "NVDA")], "U2": [_item("9999")]})
    run_per_user_push(
        store, _orch(demo_paths), _macro(),
        channel_access_token="TOKEN", as_of=AS_OF, full_watch=True,
    )
    sent = {to: text for to, text in captured_push}
    assert set(sent) == {"U1", "U2"}                   # 兩人都推（含 abstain 的 U2）
    assert "個股盯盤" in sent["U1"] and "指令" in sent["U1"]
    assert "📊 技術" in sent["U1"] and "💰 籌碼" in sent["U1"]
    assert "9999" in sent["U2"] and "資料不足" in sent["U2"]   # 查無 → 誠實不捏造


def test_empty_watchlist_user_not_pushed(demo_paths, captured_push):
    store = _MemStore({"U3": []})
    results = run_per_user_push(
        store, _orch(demo_paths), _macro(),
        channel_access_token="TOKEN", as_of=AS_OF,
    )
    assert results[0].n_tracked == 0
    assert results[0].pushed is False
    assert captured_push == []


# ---------------------------------------------------------------- dry-run / Fail-Loud
def test_dry_run_computes_but_does_not_push(demo_paths, captured_push):
    store = _MemStore({"U1": [_item("2330")]})
    results = run_per_user_push(
        store, _orch(demo_paths), _macro(),
        channel_access_token=None, as_of=AS_OF, dry_run=True,
    )
    # 有算出利多，但完全沒推
    assert results[0].n_bullish == 1
    assert results[0].pushed is False
    assert captured_push == []


def test_missing_token_raises(demo_paths):
    store = _MemStore({"U1": [_item("2330")]})
    with pytest.raises(LinePushError):
        run_per_user_push(
            store, _orch(demo_paths), _macro(),
            channel_access_token=None, as_of=AS_OF,
        )


def test_per_user_error_is_isolated(demo_paths, monkeypatch):
    # U1 推播丟錯 → 記錄該人 error，不影響流程回傳（其他人照常）
    def boom(self, text):
        raise LinePushError("模擬 API 500")

    monkeypatch.setattr(LinePusher, "push_text", boom)
    store = _MemStore({"U1": [_item("2330")]})
    results = run_per_user_push(
        store, _orch(demo_paths), _macro(),
        channel_access_token="TOKEN", as_of=AS_OF,
    )
    assert results[0].pushed is False
    assert results[0].error is not None
    assert "500" in results[0].error
