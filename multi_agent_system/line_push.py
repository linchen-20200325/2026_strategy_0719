"""line_push.py — LINE 推播（LINE Messaging API push）。

用標準庫 urllib 實作,**不新增第三方相依**;urllib 預設會套用環境變數
`HTTPS_PROXY` / `NO_PROXY`,故在 NAS/公司代理環境亦可用。

設定（環境變數,或建構子傳入）:
    LINE_CHANNEL_ACCESS_TOKEN   — Messaging API channel 的長期 access token
    LINE_TO                     — 推播對象 userId / groupId（push 需指定對象）

Fail-Loud:缺 token/對象 → raise;HTTP 非 2xx / 連線失敗 → raise（不靜默吞）。

註:LINE Notify 已於 2025 停用,本檔用的是 Messaging API 的 push endpoint。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .contracts import FinalDecision
from .notifications import format_notification, should_notify

LINE_PUSH_ENDPOINT = "https://api.line.me/v2/bot/message/push"
_MAX_TEXT_LEN = 4900   # LINE 單則 text 上限 5000,留餘裕


class LinePushError(RuntimeError):
    """LINE 推播失敗（設定缺失 / API 錯誤 / 連線失敗）。"""


class LinePusher:
    """低階 LINE push client:把一段文字推給設定的對象。"""

    def __init__(
        self,
        channel_access_token: str | None = None,
        to: str | None = None,
        *,
        timeout: float = 10.0,
    ) -> None:
        self.token = channel_access_token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
        self.to = to or os.environ.get("LINE_TO")
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.to)

    def _require_config(self) -> None:
        missing = []
        if not self.token:
            missing.append("LINE_CHANNEL_ACCESS_TOKEN")
        if not self.to:
            missing.append("LINE_TO")
        if missing:
            raise LinePushError(f"LINE 推播缺少設定：{missing}")

    def push_text(self, text: str) -> None:
        """推一則純文字訊息。失敗一律 raise LinePushError。"""
        self._require_config()
        if not text.strip():
            raise LinePushError("推播內容為空")
        payload = {
            "to": self.to,
            "messages": [{"type": "text", "text": text[:_MAX_TEXT_LEN]}],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            LINE_PUSH_ENDPOINT,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = getattr(resp, "status", None)
                if status is None:
                    status = resp.getcode()
                if status // 100 != 2:
                    body = resp.read().decode("utf-8", "replace")
                    raise LinePushError(f"LINE API 回 {status}：{body}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise LinePushError(f"LINE API HTTP {exc.code}：{body}") from exc
        except urllib.error.URLError as exc:
            raise LinePushError(f"LINE API 連線失敗：{exc.reason}") from exc


class LineNotifier:
    """實作 `Notifier` 介面:每則「可行動」訊號推一則 LINE。

    與 ConsoleNotifier 可互換,runner 換注入即可。
    """

    def __init__(self, channel_access_token: str | None = None, to: str | None = None) -> None:
        self.pusher = LinePusher(channel_access_token, to)

    def notify(self, decision: FinalDecision) -> None:
        if should_notify(decision):
            self.pusher.push_text(format_notification(decision))
