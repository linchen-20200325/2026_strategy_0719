"""test_github_store.py — GitHub-backed 訂閱清單 store（以記憶體假 API 驅動，不打真 GitHub）。"""

from __future__ import annotations

import base64
import json
import re

import pytest

from multi_agent_system.github_store import GithubSubscriberStore
from multi_agent_system.pipeline import WatchItem
from multi_agent_system.subscribers import SubscriberStoreError


class _FakeGitHub:
    """模擬 GitHub Contents API：GET 回 content+sha、PUT 帶 sha 樂觀鎖。"""

    def __init__(self):
        self.blob: dict | None = None
        self.sha: str | None = None
        self._n = 0

    def request(self, method, url, body=None):
        assert re.search(r"/contents/[^?]+", url)   # 路徑格式正確
        if method == "GET":
            if self.blob is None:
                return 404, b'{"message":"Not Found"}'
            content = base64.b64encode(json.dumps(self.blob).encode()).decode()
            return 200, json.dumps({"content": content, "sha": self.sha}).encode()
        if method == "PUT":
            if self.sha is not None and body.get("sha") != self.sha:
                return 409, b'{"message":"sha does not match"}'
            self.blob = json.loads(base64.b64decode(body["content"]).decode())
            self._n += 1
            self.sha = f"sha{self._n}"
            return 200, json.dumps({"content": body["content"], "sha": self.sha}).encode()
        return 400, b""


def _item(code="2330", cat="台股"):
    return WatchItem(code, "NVDA", ("台積電", "半導體"), 0.10, 0.20, 1.4, cat)


@pytest.fixture
def store(monkeypatch):
    s = GithubSubscriberStore("tok", "owner/repo")
    fake = _FakeGitHub()
    monkeypatch.setattr(s, "_request", fake.request)
    s._fake = fake        # 測試可檢視底層 blob
    return s


# ---------------------------------------------------------------- 建構檢查
def test_missing_token_raises():
    with pytest.raises(SubscriberStoreError):
        GithubSubscriberStore("", "owner/repo")


def test_bad_repo_raises():
    with pytest.raises(SubscriberStoreError):
        GithubSubscriberStore("tok", "no-slash")


# ---------------------------------------------------------------- 空/roundtrip
def test_empty_when_missing(store):
    assert store.user_ids() == []
    assert store.get("U1") == []


def test_set_get_roundtrip(store):
    store.set("U1", [_item("2330"), _item("2454")])
    assert store.user_ids() == ["U1"]
    assert [g.tw_stock_id for g in store.get("U1")] == ["2330", "2454"]
    # 底層確實寫成 {userId: [item dict]}，且中文關鍵字保留
    assert store._fake.blob["U1"][0]["keywords"] == ["台積電", "半導體"]


def test_add_item_dedup(store):
    store.add_item("U1", _item("2330", "台股"))
    store.add_item("U1", _item("2330", "ETF"))   # 同代號 → 更新
    items = store.get("U1")
    assert len(items) == 1
    assert items[0].category == "ETF"


def test_remove_item(store):
    store.set("U1", [_item("2330"), _item("2454")])
    assert store.remove_item("U1", "2330") is True
    assert [g.tw_stock_id for g in store.get("U1")] == ["2454"]
    assert store.remove_item("U1", "9999") is False   # 不存在 → False，不亂寫


def test_remove_user(store):
    store.set("U1", [_item()])
    store.set("U2", [_item()])
    store.remove_user("U1")
    assert store.user_ids() == ["U2"]


# ---------------------------------------------------------------- Fail-Loud
def test_http_error_raises(monkeypatch):
    s = GithubSubscriberStore("tok", "owner/repo")
    monkeypatch.setattr(s, "_request", lambda *a, **k: (500, b'{"message":"boom"}'))
    with pytest.raises(SubscriberStoreError):
        s.user_ids()


def test_put_failure_raises(monkeypatch):
    s = GithubSubscriberStore("tok", "owner/repo")

    def fake(method, url, body=None):
        if method == "GET":
            return 404, b"{}"
        return 403, b'{"message":"no write"}'   # PUT 無權限

    monkeypatch.setattr(s, "_request", fake)
    with pytest.raises(SubscriberStoreError):
        s.set("U1", [_item()])
