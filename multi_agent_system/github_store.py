"""github_store.py — 把訂閱清單存在 GitHub repo 的 JSON（真·同 mynews 的 gh_load/gh_save）。

用途：雲端 dashboard / NAS webhook / cron 共用**同一份** subscribers.json —— 存在 repo 裡,
誰改都看得到（GitHub Contents API 讀寫）。實作與 `JsonSubscriberStore` 相同的 `SubscriberStore`
介面（見 subscribers.py），故上層（dashboard / run_pipeline --per-user / CLI）不需知道 backend。

零新相依：只用標準庫 urllib（同 mynews nas_line_bot.py）+ 既有 GITHUB_TOKEN。

Fail-Loud：缺 token / API 非 2xx / 格式錯 → raise SubscriberStoreError（不靜默）。
404（repo 尚無此檔）→ 視為空清單（首次寫入時建立）。寫入用 sha 樂觀鎖，衝突即 raise。

⚠️ 隱私：userId 會寫進 repo 的 JSON（同 mynews watchlist.json）——僅適用**私有 repo**。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from .pipeline.watchlist import WatchItem
from .subscribers import SubscriberStoreError, item_from_dict, item_to_dict

_GITHUB_API = "https://api.github.com"


class GithubSubscriberStore:
    """以 GitHub repo 內單一 JSON 檔儲存 {userId: [item, ...]}。讀寫走 Contents API。"""

    def __init__(
        self,
        token: str,
        repo: str,
        *,
        path: str = "subscribers.json",
        branch: str = "main",
        timeout: float = 30.0,
    ) -> None:
        if not token:
            raise SubscriberStoreError("GithubSubscriberStore 需要 GITHUB_TOKEN")
        if not repo or "/" not in repo:
            raise SubscriberStoreError(f"GITHUB_REPO 格式應為 owner/name，收到 {repo!r}")
        self.token = token
        self.repo = repo
        self.path = path
        self.branch = branch
        self.timeout = timeout

    # ── HTTP（抽成一個方法，測試可 monkeypatch，不需真打 GitHub）───────────────
    def _request(self, method: str, url: str, body: dict | None = None) -> tuple[int, bytes]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = getattr(resp, "status", None)
                if status is None:
                    status = resp.getcode()
                return status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise SubscriberStoreError(f"GitHub API 連線失敗：{exc.reason}") from exc

    # ── 讀取：回 (data, sha)。404 → ({}, None)。───────────────────────────────
    def _load(self) -> tuple[dict[str, list[dict]], str | None]:
        url = f"{_GITHUB_API}/repos/{self.repo}/contents/{self.path}?ref={self.branch}"
        status, raw = self._request("GET", url)
        if status == 404:
            return {}, None
        if status // 100 != 2:
            raise SubscriberStoreError(
                f"讀 {self.path} 失敗 HTTP {status}：{raw.decode('utf-8', 'replace')[:300]}"
            )
        payload = json.loads(raw)
        content = base64.b64decode(payload.get("content", "")).decode("utf-8")
        data = json.loads(content) if content.strip() else {}
        if not isinstance(data, dict):
            raise SubscriberStoreError(f"{self.path} 格式錯誤（非物件）")
        return data, payload.get("sha")

    # ── 寫入：PUT 全檔（帶 sha 樂觀鎖）。────────────────────────────────────────
    def _save(self, data: dict, sha: str | None, message: str) -> None:
        url = f"{_GITHUB_API}/repos/{self.repo}/contents/{self.path}"
        content = json.dumps(data, ensure_ascii=False, indent=2)
        body: dict = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        status, raw = self._request("PUT", url, body)
        if status // 100 != 2:
            raise SubscriberStoreError(
                f"寫 {self.path} 失敗 HTTP {status}：{raw.decode('utf-8', 'replace')[:300]}"
            )

    # ── SubscriberStore 介面 ──────────────────────────────────────────────────
    def user_ids(self) -> list[str]:
        return list(self._load()[0].keys())

    def get(self, user_id: str) -> list[WatchItem]:
        data, _ = self._load()
        return [item_from_dict(d) for d in data.get(user_id, [])]

    def set(self, user_id: str, items: list[WatchItem]) -> None:
        if not user_id:
            raise ValueError("user_id 不可為空")
        data, sha = self._load()
        data[user_id] = [item_to_dict(it) for it in items]
        self._save(data, sha, f"subscribers: set {user_id} ({len(items)} 檔)")

    def add_item(self, user_id: str, item: WatchItem) -> None:
        if not user_id:
            raise ValueError("user_id 不可為空")
        data, sha = self._load()
        cur = [item_from_dict(d) for d in data.get(user_id, [])]
        cur = [it for it in cur if it.tw_stock_id != item.tw_stock_id]  # 同代號視為更新
        cur.append(item)
        data[user_id] = [item_to_dict(it) for it in cur]
        self._save(data, sha, f"subscribers: add {item.tw_stock_id} for {user_id}")

    def remove_item(self, user_id: str, tw_stock_id: str) -> bool:
        data, sha = self._load()
        cur = data.get(user_id, [])
        kept = [d for d in cur if str(d.get("tw_stock_id")) != tw_stock_id]
        if len(kept) == len(cur):
            return False
        data[user_id] = kept
        self._save(data, sha, f"subscribers: remove {tw_stock_id} for {user_id}")
        return True

    def remove_user(self, user_id: str) -> None:
        data, sha = self._load()
        if data.pop(user_id, None) is not None:
            self._save(data, sha, f"subscribers: remove user {user_id}")
