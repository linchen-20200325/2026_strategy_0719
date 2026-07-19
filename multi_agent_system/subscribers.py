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
