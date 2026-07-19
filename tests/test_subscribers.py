"""test_subscribers.py — 訂閱者 JSON 儲存（roundtrip / dedup / 壞檔 Fail-Loud）。"""

from __future__ import annotations

import pytest

from multi_agent_system.pipeline import WatchItem
from multi_agent_system.subscribers import (
    JsonSubscriberStore,
    SubscriberStoreError,
    item_from_dict,
    item_to_dict,
)


def _item(code="2330", cat="台股"):
    return WatchItem(code, "NVDA", ("台積電", "半導體"), 0.10, 0.20, 1.4, cat)


def test_item_dict_roundtrip():
    it = _item()
    back = item_from_dict(item_to_dict(it))
    assert back == it


def test_item_from_dict_requires_code():
    with pytest.raises(SubscriberStoreError):
        item_from_dict({"us_stock_id": "NVDA"})


def test_store_set_get(tmp_path):
    store = JsonSubscriberStore(str(tmp_path / "subs.json"))
    store.set("U1", [_item("2330"), _item("2454")])
    assert store.user_ids() == ["U1"]
    got = store.get("U1")
    assert [g.tw_stock_id for g in got] == ["2330", "2454"]


def test_store_add_item_dedup(tmp_path):
    store = JsonSubscriberStore(str(tmp_path / "subs.json"))
    store.add_item("U1", _item("2330", "台股"))
    store.add_item("U1", _item("2330", "ETF"))  # 同代號 → 更新,不重複
    items = store.get("U1")
    assert len(items) == 1
    assert items[0].category == "ETF"


def test_store_remove_user(tmp_path):
    store = JsonSubscriberStore(str(tmp_path / "subs.json"))
    store.set("U1", [_item()])
    store.set("U2", [_item()])
    store.remove_user("U1")
    assert store.user_ids() == ["U2"]


def test_store_missing_file_is_empty(tmp_path):
    store = JsonSubscriberStore(str(tmp_path / "nope.json"))
    assert store.user_ids() == []
    assert store.get("U1") == []


def test_store_corrupt_file_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not valid json", encoding="utf-8")
    store = JsonSubscriberStore(str(path))
    with pytest.raises(SubscriberStoreError):
        store.user_ids()


def test_store_non_object_raises(tmp_path):
    path = tmp_path / "arr.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    store = JsonSubscriberStore(str(path))
    with pytest.raises(SubscriberStoreError):
        store.user_ids()
