"""test_subscribers.py — 訂閱者 JSON 儲存（roundtrip / dedup / 壞檔 Fail-Loud）。"""

from __future__ import annotations

import json

import pytest

from multi_agent_system.github_store import GithubSubscriberStore
from multi_agent_system.pipeline import WatchItem
from multi_agent_system.subscribers import (
    JsonSubscriberStore,
    SubscriberStoreError,
    item_from_dict,
    item_to_dict,
    make_subscriber_store,
    normalize_doc,
    store_is_github,
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


# ---------------------------------------------------------------- 授權名單（同一份 JSON）
def test_allow_grant_revoke_and_ids(tmp_path):
    store = JsonSubscriberStore(str(tmp_path / "w.json"))
    assert store.allow_ids() == set()
    ok, msg = store.grant("Ufriendxxxx", "小明")
    assert ok and "已授權" in msg
    assert store.allow_ids() == {"Ufriendxxxx"}
    assert store.grant("Ufriendxxxx")[0] is False       # 重複
    assert store.grant("bad")[0] is False               # 非 U 開頭 → 拒絕
    assert store.revoke("Ufriendxxxx")[0] is True
    assert store.allow_ids() == set()
    assert store.revoke("Ufriendxxxx")[0] is False      # 不在名單


def test_allow_and_users_coexist_in_one_file(tmp_path):
    path = tmp_path / "w.json"
    store = JsonSubscriberStore(str(path))
    store.set("U1", [_item("2330")])
    store.grant("Uadminxxxx1", "管理員")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw.keys()) == {"users", "allow"}        # 同一份 JSON 兩區塊
    assert raw["users"]["U1"][0]["tw_stock_id"] == "2330"
    assert raw["allow"][0]["id"] == "Uadminxxxx1"


# ---------------------------------------------------------------- 舊格式遷移
def test_normalize_doc_migrates_legacy_flat():
    doc = normalize_doc({"U1": [{"tw_stock_id": "2330"}]})
    assert doc == {"users": {"U1": [{"tw_stock_id": "2330"}]}, "allow": []}


def test_legacy_flat_file_is_readable_then_upgraded(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text('{"U1": [{"tw_stock_id": "2330"}]}', encoding="utf-8")
    store = JsonSubscriberStore(str(path))
    assert store.user_ids() == ["U1"]                   # 舊格式可讀
    store.add_item("U1", _item("2454"))                 # 寫入即升級
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "users" in raw and "allow" in raw


# ---------------------------------------------------------------- backend factory
def test_factory_local_by_default(tmp_path):
    env = {"SUBSCRIBERS_FILE": str(tmp_path / "s.json")}
    store = make_subscriber_store(get_env=env.get)
    assert isinstance(store, JsonSubscriberStore)
    assert not store_is_github(get_env=env.get)


def test_factory_local_path_overrides_env(tmp_path):
    store = make_subscriber_store(get_env={}.get, local_path=str(tmp_path / "x.json"))
    assert isinstance(store, JsonSubscriberStore)
    assert store.path.endswith("x.json")


def test_factory_github_when_token_and_repo():
    env = {"GITHUB_TOKEN": "t", "GITHUB_REPO": "owner/repo"}
    store = make_subscriber_store(get_env=env.get)
    assert isinstance(store, GithubSubscriberStore)
    assert store_is_github(get_env=env.get)


def test_factory_explicit_local_beats_github_creds():
    env = {"SUBSCRIBERS_BACKEND": "local", "GITHUB_TOKEN": "t", "GITHUB_REPO": "o/r"}
    store = make_subscriber_store(get_env=env.get)
    assert isinstance(store, JsonSubscriberStore)   # 明示 local 蓋過 github 憑證


# ---------------------------------------------------------------- GITHUB_TOKEN_FILE（同 mynews）
def test_factory_github_token_from_file(tmp_path):
    tok = tmp_path / "gh.token"
    tok.write_text("github_pat_fromfile\n", encoding="utf-8")    # 尾端換行 → 應被 strip
    env = {"GITHUB_TOKEN_FILE": str(tok), "GITHUB_REPO": "owner/repo"}
    store = make_subscriber_store(get_env=env.get)
    assert isinstance(store, GithubSubscriberStore)
    assert store.token == "github_pat_fromfile"                  # 從檔案讀入 + strip
    assert store_is_github(get_env=env.get)


def test_factory_github_token_env_beats_file(tmp_path):
    tok = tmp_path / "gh.token"
    tok.write_text("from_file", encoding="utf-8")
    env = {"GITHUB_TOKEN": "from_env", "GITHUB_TOKEN_FILE": str(tok), "GITHUB_REPO": "o/r"}
    store = make_subscriber_store(get_env=env.get)
    assert store.token == "from_env"                            # 環境變數優先於檔案


def test_factory_github_token_file_unreadable_raises(tmp_path):
    env = {"GITHUB_TOKEN_FILE": str(tmp_path / "nope.token"), "GITHUB_REPO": "o/r"}
    with pytest.raises(SubscriberStoreError):                   # 檔案設了卻讀不到 → Fail-Loud
        make_subscriber_store(get_env=env.get)
