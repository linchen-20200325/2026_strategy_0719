"""http.py — 共用 HTTP 請求底層（stdlib urllib，無第三方相依）。L0/L1 infra。

把散在 line_push / github_store / nas_line_bot 的 urllib 請求 scaffolding + status 抽取
（`getattr(resp,"status") or getcode()`）+ HTTPError→(code,body) 統一於一處，避免三份
網路/錯誤處理各自漂移。urllib 自動套用 `HTTPS_PROXY` / `NO_PROXY`（NAS/公司代理可用）。

錯誤語意（呼叫端各自包成自身型別 / 決定 raise 或 warn）：
* 有 HTTP 回應（含 4xx/5xx）→ 回 (status, body)，**不 raise**（GitHub 用 404 當正常訊號）。
* 連線層失敗（URLError，無回應）→ raise `HttpError`。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class HttpError(RuntimeError):
    """HTTP 連線層失敗（URLError，無 HTTP 回應）。呼叫端可包成自身錯誤型別。"""


def request_json(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, bytes]:
    """通用 JSON HTTP 請求 → (status, body_bytes)。

    body 有值 → json 序列化為 request body（呼叫端自備 Content-Type header）。
    非 2xx（有 HTTP 回應）不 raise → 回 (code, body) 由呼叫端判讀；URLError → raise HttpError。
    """
    data = (
        json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    )
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        # 用 urllib.request.urlopen（模組屬性）而非 from-import → 測試可 monkeypatch。
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None)
            if status is None:
                status = resp.getcode()
            return status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise HttpError(str(exc.reason)) from exc
