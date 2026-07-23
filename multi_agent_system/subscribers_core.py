"""subscribers_core.py — 訂閱者儲存的純邏輯 SSOT（DTO-map / 遷移 / 授權 / Store 介面）。L1。

抽出 subscribers.py 與 github_store.py **共用**的純函式 + Protocol，讓兩個 backend 都
import 本檔 → 打破 `subscribers` ⇄ `github_store` 依賴循環（原靠 lazy import 硬撐，V7）。
無 I/O、無 backend 相依。
"""

from __future__ import annotations

from typing import Protocol

from config import DEFAULT_MAX_WEIGHT_RATIO, DEFAULT_WEIGHT_RATIO

from .contracts import WatchItem


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


# ── 文件正規化 / 遷移（舊 {userId:[...]} → {"users":{...},"allow":[]}）──────────
def normalize_doc(raw: object) -> dict:
    """任意載入結果 → 標準 `{"users": {...}, "allow": [...]}`。

    新格式（含 dict 型別的 "users" 鍵）原樣採用；否則視為舊「頂層即 users」格式並包一層。
    """
    if not isinstance(raw, dict):
        raise SubscriberStoreError("watchlist JSON 格式錯誤（非物件）")
    if isinstance(raw.get("users"), dict):
        users = raw["users"]
        allow = raw.get("allow")
    else:
        users = raw          # 舊格式：頂層直接是 {userId: [items]}
        allow = None
    if not isinstance(users, dict):
        raise SubscriberStoreError("watchlist JSON 的 users 非物件")
    return {"users": users, "allow": allow if isinstance(allow, list) else []}


# ── 授權名單純邏輯（SSOT：Json / Github 兩後端 + webhook 共用；可單測）─────────
def valid_user_id(uid: str) -> bool:
    """LINE userId：U 開頭 + 至少 10 碼（U + 32 hex 實務；放寬為 >=10 容錯）。"""
    return bool(uid) and uid.startswith("U") and len(uid) >= 10


def allow_ids_of(allow: list[dict]) -> set[str]:
    return {str(a.get("id")) for a in allow if a.get("id")}


def apply_grant(allow: list[dict], uid: str, name: str = "") -> tuple[bool, str]:
    """就地新增授權；回 (是否有變更, 給使用者的訊息)。不合法 / 重複 → 不變更。"""
    uid = (uid or "").strip()
    if not valid_user_id(uid):
        return False, f"看不懂 userId「{uid}」，請貼完整的 U 開頭那串。"
    for a in allow:
        if str(a.get("id")) == uid:
            return False, f"{(a.get('name') or uid[:8] + '…')} 已在授權名單內。"
    allow.append({"id": uid, "name": (name or "").strip()})
    who = (name.strip() + " ") if name.strip() else ""
    return True, f"✅ 已授權 {who}{uid[:8]}…"


def apply_revoke(allow: list[dict], uid: str) -> tuple[bool, str]:
    """就地移除授權；回 (是否有變更, 訊息)。"""
    uid = (uid or "").strip()
    before = len(allow)
    allow[:] = [a for a in allow if str(a.get("id")) != uid]
    if len(allow) == before:
        return False, f"{uid[:8]}… 不在授權名單內。"
    return True, f"🗑️ 已撤銷 {uid[:8]}…"


def format_allow_list(allow: list[dict]) -> str:
    if not allow:
        return "授權名單目前是空的（此時以環境變數 STRATEGY_ALLOW_USER 為準）。"
    lines = [f"🔑 授權名單（{len(allow)} 人）："]
    for a in allow:
        nm = (a.get("name") or "").strip()
        lines.append(f"・{(nm + '  ') if nm else ''}{a.get('id', '')}")
    lines.append("")
    lines.append("指令：授權 <userId> [名字] / 撤銷 <userId> / 名單")
    return "\n".join(lines)


class SubscriberStore(Protocol):
    # 每人清單
    def user_ids(self) -> list[str]: ...
    def get(self, user_id: str) -> list[WatchItem]: ...
    def set(self, user_id: str, items: list[WatchItem]) -> None: ...
    def add_item(self, user_id: str, item: WatchItem) -> None: ...
    def remove_item(self, user_id: str, tw_stock_id: str) -> bool: ...
    def remove_user(self, user_id: str) -> None: ...
    # 授權名單（存同一份 JSON 的 allow 欄位）
    def allow_ids(self) -> set[str]: ...
    def grant(self, user_id: str, name: str = "") -> tuple[bool, str]: ...
    def revoke(self, user_id: str) -> tuple[bool, str]: ...
    def allow_text(self) -> str: ...
