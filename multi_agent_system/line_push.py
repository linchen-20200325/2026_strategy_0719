"""line_push.py — LINE 推播（LINE Messaging API push）。

用標準庫 urllib 實作,**不新增第三方相依**;urllib 預設會套用環境變數
`HTTPS_PROXY` / `NO_PROXY`,故在 NAS/公司代理環境亦可用。

設定（環境變數,或建構子傳入）:
    LINE_CHANNEL_ACCESS_TOKEN   — Messaging API channel 的長期 access token
    LINE_TO                     — 推播對象,依值自動選端點（參考 mynews/line_notify.py 慣例）:
        · "broadcast"                → /broadcast,發給所有加好友的人（免收集 ID）
        · 多個 ID（逗號/空白分隔）    → /multicast,發給名單（最多 500 人）
        · 單一 ID（user/group/room） → /push

Fail-Loud:缺 token/對象 → raise;HTTP 非 2xx / 連線失敗 → raise（不靜默吞）。

註:LINE Notify 已於 2025 停用,本檔用的是 Messaging API。
"""

from __future__ import annotations

import logging
import os
import re

from .contracts import FinalDecision
from .infra.http import HttpError, request_json
from .notifications import format_notification, should_notify

logger = logging.getLogger("multi_agent_system.line")

# 三端點 + LINE_TO 自動路由（參考 mynews/line_notify.py 的推播慣例）
LINE_PUSH_ENDPOINT = "https://api.line.me/v2/bot/message/push"
LINE_MULTICAST_ENDPOINT = "https://api.line.me/v2/bot/message/multicast"
LINE_BROADCAST_ENDPOINT = "https://api.line.me/v2/bot/message/broadcast"
MAX_LINE_TEXT_LEN = 4900   # LINE 單則 text 上限 5000,留餘裕（SSOT，nas_line_bot 共用）


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

    def _resolve_target(self, messages: list[dict]) -> tuple[str, dict, str]:
        """依 self.to 決定端點與 body（broadcast / multicast / push,參考 mynews 慣例）。"""
        to_raw = str(self.to).strip()
        if to_raw.lower() == "broadcast":
            return LINE_BROADCAST_ENDPOINT, {"messages": messages}, "broadcast(全體好友)"
        ids = [t for t in re.split(r"[,\s]+", to_raw) if t]
        if not ids:
            raise LinePushError("LINE_TO 無有效推播對象")
        if len(ids) > 1:
            return (
                LINE_MULTICAST_ENDPOINT,
                {"to": ids, "messages": messages},
                f"multicast({len(ids)} 人名單)",
            )
        return LINE_PUSH_ENDPOINT, {"to": ids[0], "messages": messages}, "push(單一對象)"

    def push_text(self, text: str) -> None:
        """推一則純文字訊息;依 LINE_TO 自動 broadcast/multicast/push。失敗一律 raise。"""
        self._require_config()
        if not text.strip():
            raise LinePushError("推播內容為空")
        messages = [{"type": "text", "text": text[:MAX_LINE_TEXT_LEN]}]
        endpoint, body, mode = self._resolve_target(messages)
        logger.info("LINE 推播模式：%s", mode)  # 只印模式,不印任何 ID（避免外洩）
        try:
            status, raw = request_json(
                "POST", endpoint, body=body, timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.token}",
                },
            )
        except HttpError as exc:
            raise LinePushError(f"LINE API 連線失敗：{exc}") from exc
        if status // 100 != 2:
            raise LinePushError(f"LINE API 回 {status}：{raw.decode('utf-8', 'replace')}")


class LineNotifier:
    """實作 `Notifier` 介面:每則「可行動」訊號推一則 LINE。

    與 ConsoleNotifier 可互換,runner 換注入即可。
    """

    def __init__(self, channel_access_token: str | None = None, to: str | None = None) -> None:
        self.pusher = LinePusher(channel_access_token, to)

    def notify(self, decision: FinalDecision) -> None:
        if should_notify(decision):
            self.pusher.push_text(format_notification(decision))
