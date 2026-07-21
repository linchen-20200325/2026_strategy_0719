"""test_nas_line_bot.py — LINE webhook bot：指令解析、驗簽、加/刪/清單、授權。

只測純邏輯 + 注入 store 的 handle_text（不起真的 HTTP 服務、不打 LINE API）。
授權名單存在同一個 store（共用 watchlist.json 的 allow 欄位）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from multi_agent_system.subscribers import JsonSubscriberStore
from scripts.nas_line_bot import (
    _mask_uid,
    handle_text,
    normalize_ticker,
    parse_add,
    parse_admin,
    parse_command,
    verify_signature,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    # 隔離執行環境可能已設的授權變數，讓 open-mode 測試乾淨。
    for k in ("STRATEGY_ALLOW_USER", "STRATEGY_ADMIN_USER"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def store(tmp_path):
    return JsonSubscriberStore(str(tmp_path / "watchlist.json"))


# ---------------------------------------------------------------- 純解析
def test_parse_command():
    assert parse_command("id") == ("id", "")
    assert parse_command("加 2330 台積電") == ("add", "2330 台積電")
    assert parse_command("刪 2330") == ("remove", "2330")
    assert parse_command("清單") == ("list", "")
    assert parse_command("哈囉") == ("help", "哈囉")


def test_parse_add_category_code_name():
    assert parse_add("2330 台積電") == ("台股", "2330", "台積電")
    assert parse_add("ETF 0050") == ("ETF", "0050", "")
    assert parse_add("基金 ab12") == ("基金", "AB12", "")
    assert parse_add("台股 2330") == ("台股", "2330", "")
    assert parse_add("")[1] == ""
    assert parse_add("台積電")[1] == ""   # 無數字代號 → code 空


def test_normalize_ticker():
    assert normalize_ticker("2330") == "2330"
    assert normalize_ticker("買 0050 好") == "0050"
    assert normalize_ticker("abc") == ""


def test_parse_admin():
    assert parse_admin("授權 U123 小明") == ("grant", "U123 小明")
    assert parse_admin("撤銷 U123") == ("revoke", "U123")
    assert parse_admin("名單") == ("allowlist", "")
    assert parse_admin("授權名單") == ("allowlist", "")   # 完整詞優先
    assert parse_admin("加 2330") == ("", "加 2330")


def test_mask_uid():
    assert _mask_uid("Uabcdefgh1234567890") == "Uabcdefg…"   # 只留前 8 碼
    assert _mask_uid("") == "?"


# ---------------------------------------------------------------- 驗簽
def test_verify_signature_roundtrip():
    secret, body = "s3cr3t", b'{"events":[]}'
    good = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    assert verify_signature(secret, body, good) is True
    assert verify_signature(secret, body, "wrong") is False
    assert verify_signature("", body, good) is False   # 無 secret → 一律 False


# ---------------------------------------------------------------- handle_text 加/刪/清單
def _reply(store, text, uid="Uuser"):
    return handle_text(text, uid, store=store)


def test_id_returns_userid(store):
    out = _reply(store, "id", uid="Uabc123")
    assert "Uabc123" in out


def test_add_then_list_then_remove(store):
    assert "已加入" in _reply(store, "加 2330 台積電")
    items = store.get("Uuser")
    assert [it.tw_stock_id for it in items] == ["2330"]
    assert items[0].keywords == ("台積電",)    # 名稱→新聞關鍵字
    assert items[0].category == "台股"

    assert "2330" in _reply(store, "清單")

    assert "已移除" in _reply(store, "刪 2330")
    assert store.get("Uuser") == []
    assert "不在你的清單內" in _reply(store, "刪 2330")   # 再刪 → 誠實回報


def test_add_etf_category(store):
    _reply(store, "加 ETF 0050 元大台灣50")
    it = store.get("Uuser")[0]
    assert it.category == "ETF"
    assert it.tw_stock_id == "0050"


def test_add_gibberish_is_rejected(store):
    assert "看不懂" in _reply(store, "加 台積電")   # 無數字代號
    assert store.get("Uuser") == []                  # 不寫入


def test_empty_list_message(store):
    assert "空的" in _reply(store, "清單")


def test_help_fallback(store):
    assert "盯盤指令" in _reply(store, "你好嗎")


# ---------------------------------------------------------------- 授權
def test_unauthorized_user_blocked(store, monkeypatch):
    monkeypatch.setenv("STRATEGY_ALLOW_USER", "Uallowed")
    out = handle_text("加 2330", "Ustranger", store=store)
    assert "還沒被授權" in out
    assert store.get("Ustranger") == []      # 未授權者不得寫入
    # 但 id 一律可回（好友自助取得 userId）
    assert "Ustranger" in handle_text("id", "Ustranger", store=store)


def test_allowed_user_can_add(store, monkeypatch):
    monkeypatch.setenv("STRATEGY_ALLOW_USER", "Uvip")
    assert "已加入" in handle_text("加 2330", "Uvip", store=store)
    assert [it.tw_stock_id for it in store.get("Uvip")] == ["2330"]


def test_admin_grant_and_revoke(store, monkeypatch):
    monkeypatch.setenv("STRATEGY_ADMIN_USER", "Uadmin")
    # 管理員授權一位好友 → 寫進同一份 store 的 allow
    out = handle_text("授權 Ufriendxxxx 小明", "Uadmin", store=store)
    assert "已授權" in out
    assert "Ufriendxxxx" in store.allow_ids()
    # 被授權者立即可用（不必重啟）
    assert "已加入" in handle_text("加 2330", "Ufriendxxxx", store=store)
    # 非管理員不能授權
    denied = handle_text("授權 Uother12345", "Urando", store=store)
    assert "沒有權限" in denied
    # 撤銷
    out2 = handle_text("撤銷 Ufriendxxxx", "Uadmin", store=store)
    assert "已撤銷" in out2
    assert "Ufriendxxxx" not in store.allow_ids()


def test_admin_allowlist_shows_names(store, monkeypatch):
    monkeypatch.setenv("STRATEGY_ADMIN_USER", "Uadmin")
    handle_text("授權 Ufriendxxxx 小明", "Uadmin", store=store)
    out = handle_text("名單", "Uadmin", store=store)
    assert "小明" in out and "授權名單" in out
