"""test_line_push.py — LINE 推播（mock HTTP,不打真實網路）。"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from multi_agent_system.contracts import Action, FinalDecision
from multi_agent_system.line_push import (
    LINE_BROADCAST_ENDPOINT,
    LINE_MULTICAST_ENDPOINT,
    LINE_PUSH_ENDPOINT,
    LineNotifier,
    LinePusher,
    LinePushError,
)


def _decision(action=Action.STRONG_BUY, score=0.87, *, abstained=False):
    return FinalDecision("2330", action, score, abstained, False, {}, "rationale")


class _Resp:
    status = 200

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_not_configured_raises(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINE_TO", raising=False)
    p = LinePusher()
    assert not p.is_configured
    with pytest.raises(LinePushError):
        p.push_text("hi")


def test_push_text_success_builds_correct_request(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _Resp()

    monkeypatch.setattr(
        "multi_agent_system.infra.http.urllib.request.urlopen", fake_urlopen
    )
    LinePusher("TOKEN", "U123").push_text("hello")

    req = captured["req"]
    assert req.full_url == LINE_PUSH_ENDPOINT
    assert req.method == "POST"
    assert req.get_header("Authorization") == "Bearer TOKEN"
    body = json.loads(req.data)
    assert body == {"to": "U123", "messages": [{"type": "text", "text": "hello"}]}


def _capture(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _Resp()

    monkeypatch.setattr(
        "multi_agent_system.infra.http.urllib.request.urlopen", fake_urlopen
    )
    return captured


def test_route_broadcast(monkeypatch):
    captured = _capture(monkeypatch)
    LinePusher("TOKEN", "broadcast").push_text("hi")
    req = captured["req"]
    assert req.full_url == LINE_BROADCAST_ENDPOINT
    assert json.loads(req.data) == {"messages": [{"type": "text", "text": "hi"}]}  # 無 to


def test_route_multicast(monkeypatch):
    captured = _capture(monkeypatch)
    LinePusher("TOKEN", "U1, U2 U3").push_text("hi")  # 逗號/空白混合
    req = captured["req"]
    assert req.full_url == LINE_MULTICAST_ENDPOINT
    body = json.loads(req.data)
    assert body["to"] == ["U1", "U2", "U3"]


def test_route_push_single(monkeypatch):
    captured = _capture(monkeypatch)
    LinePusher("TOKEN", "Uonly").push_text("hi")
    assert captured["req"].full_url == LINE_PUSH_ENDPOINT


def test_push_text_http_error_raises(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            "url", 401, "Unauthorized", {}, io.BytesIO(b'{"message":"invalid token"}')
        )

    monkeypatch.setattr(
        "multi_agent_system.infra.http.urllib.request.urlopen", fake_urlopen
    )
    with pytest.raises(LinePushError) as ei:
        LinePusher("T", "U").push_text("hi")
    assert "401" in str(ei.value)


def test_push_text_conn_error_raises(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(
        "multi_agent_system.infra.http.urllib.request.urlopen", fake_urlopen
    )
    with pytest.raises(LinePushError):
        LinePusher("T", "U").push_text("hi")


def test_push_text_empty_raises():
    with pytest.raises(LinePushError):
        LinePusher("T", "U").push_text("   ")


def test_line_notifier_pushes_only_actionable(monkeypatch):
    sent = []
    monkeypatch.setattr(LinePusher, "push_text", lambda self, text: sent.append(text))
    n = LineNotifier("T", "U")
    n.notify(_decision(Action.STRONG_BUY, 0.9))          # 推
    n.notify(_decision(Action.HOLD, 0.5))                # 不推
    n.notify(_decision(Action.HOLD, None, abstained=True))  # 不推
    assert len(sent) == 1
    assert "2330" in sent[0]


def test_configured_from_env(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "envtok")
    monkeypatch.setenv("LINE_TO", "envto")
    p = LinePusher()
    assert p.is_configured
    assert p.token == "envtok" and p.to == "envto"
