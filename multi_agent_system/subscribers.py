"""subscribers.py — 每位 LINE 使用者的追蹤清單儲存（userId → WatchItem 清單）。

用途：個人化推播 —— 每個 user 各自選標的，收自己的訊號（走 LINE push 逐人）。

儲存：先用 JSON 檔（原子寫入）;`SubscriberStore` 為介面,之後可換 Google Sheet / DB
而不動上層邏輯。**入站收集**（好友傳訊選標的的 LINE webhook / LIFF）屬另一支服務,
本檔只負責「存 + 取」,可由 webhook / CLI / Sheet 任一來源寫入。

Fail-Loud：檔案損毀 / 格式錯誤 → raise,不靜默吞。
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Protocol

from config import DEFAULT_MAX_WEIGHT_RATIO, DEFAULT_WEIGHT_RATIO

from .pipeline.watchlist import WatchItem


class SubscriberStoreError(RuntimeError):
    """訂閱者儲存層錯誤（壞檔 / 格式不符）。"""


def item_to_dict(item: WatchItem) -> dict:
    return {
        "tw_stock_id": item.tw_stock_id,
        "us_stock_id": item.us_stock_id,
        "keywords": list(item.keywords),
        "current_weight_ratio": item.current_weight_ratio,
        "max_weight_ratio": item.max_weight_ratio,
        "sharpe": item.sharpe,
        "category": item.category,
    }


def item_from_dict(d: dict) -> WatchItem:
    if not d.get("tw_stock_id"):
        raise SubscriberStoreError(f"訂閱項缺 tw_stock_id：{d}")
    return WatchItem(
        tw_stock_id=str(d["tw_stock_id"]),
        us_stock_id=str(d.get("us_stock_id", "") or ""),
        keywords=tuple(d.get("keywords", []) or ()),
        current_weight_ratio=float(d.get("current_weight_ratio", DEFAULT_WEIGHT_RATIO)),
        max_weight_ratio=float(d.get("max_weight_ratio", DEFAULT_MAX_WEIGHT_RATIO)),
        sharpe=None if d.get("sharpe") is None else float(d["sharpe"]),
        category=str(d.get("category", "台股") or "台股"),
    )


class SubscriberStore(Protocol):
    def user_ids(self) -> list[str]: ...
    def get(self, user_id: str) -> list[WatchItem]: ...
    def set(self, user_id: str, items: list[WatchItem]) -> None: ...
    def add_item(self, user_id: str, item: WatchItem) -> None: ...
    def remove_item(self, user_id: str, tw_stock_id: str) -> bool: ...
    def remove_user(self, user_id: str) -> None: ...


class JsonSubscriberStore:
    """以單一 JSON 檔儲存 {userId: [item, ...]}。原子寫入,壞檔即 raise。"""

    def __init__(self, path: str) -> None:
        self.path = path

    def _load(self) -> dict[str, list[dict]]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise SubscriberStoreError(f"讀取訂閱檔失敗 {self.path}：{exc}") from exc
        if not isinstance(data, dict):
            raise SubscriberStoreError(f"訂閱檔格式錯誤（非物件）：{self.path}")
        return data

    def _save(self, data: dict) -> None:
        # 原子寫入：先寫暫存再 replace,避免半寫壞檔。
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def user_ids(self) -> list[str]:
        return list(self._load().keys())

    def get(self, user_id: str) -> list[WatchItem]:
        return [item_from_dict(d) for d in self._load().get(user_id, [])]

    def set(self, user_id: str, items: list[WatchItem]) -> None:
        if not user_id:
            raise ValueError("user_id 不可為空")
        data = self._load()
        data[user_id] = [item_to_dict(it) for it in items]
        self._save(data)

    def add_item(self, user_id: str, item: WatchItem) -> None:
        items = self.get(user_id)
        # 同代號視為更新（去重）
        items = [it for it in items if it.tw_stock_id != item.tw_stock_id]
        items.append(item)
        self.set(user_id, items)

    def remove_item(self, user_id: str, tw_stock_id: str) -> bool:
        """移除某 user 的一檔;回傳是否有移除。清單清空仍保留該 user（可再加）。"""
        items = self.get(user_id)
        kept = [it for it in items if it.tw_stock_id != tw_stock_id]
        if len(kept) == len(items):
            return False
        self.set(user_id, kept)
        return True

    def remove_user(self, user_id: str) -> None:
        data = self._load()
        if data.pop(user_id, None) is not None:
            self._save(data)


def make_subscriber_store(*, get_env=None, local_path: str | None = None) -> SubscriberStore:
    """依環境變數選 backend（dashboard / cron / webhook 共用一個入口 → SSOT）。

    - SUBSCRIBERS_BACKEND=github（或未設但有 GITHUB_TOKEN + GITHUB_REPO）→ GithubSubscriberStore
      （雲端 + NAS 共用 repo 內 JSON）。用 SUBSCRIBERS_REPO_PATH（預設 subscribers.json）/ GITHUB_BRANCH。
    - 否則 → JsonSubscriberStore（本機檔，路徑 local_path > SUBSCRIBERS_FILE > subscribers.json）。

    get_env 可注入（測試 / 讀 st.secrets 用），預設讀 os.environ。
    local_path 由 CLI flag（--subscribers / --store）傳入，優先於 SUBSCRIBERS_FILE。
    """
    env = get_env or os.environ.get
    backend = (env("SUBSCRIBERS_BACKEND") or "").strip().lower()
    token, repo = env("GITHUB_TOKEN"), env("GITHUB_REPO")
    use_github = backend == "github" or (not backend and token and repo)
    if use_github:
        from .github_store import GithubSubscriberStore  # lazy：避免循環 import

        return GithubSubscriberStore(
            token or "", repo or "",
            path=env("SUBSCRIBERS_REPO_PATH") or "subscribers.json",
            branch=env("GITHUB_BRANCH") or "main",
        )
    return JsonSubscriberStore(local_path or env("SUBSCRIBERS_FILE") or "subscribers.json")


def store_is_github(*, get_env=None) -> bool:
    """上層（dashboard）判斷是否已設定 GitHub 持久化（用來決定要不要顯示『存得住』）。"""
    env = get_env or os.environ.get
    backend = (env("SUBSCRIBERS_BACKEND") or "").strip().lower()
    return backend == "github" or (not backend and bool(env("GITHUB_TOKEN")) and bool(env("GITHUB_REPO")))
