"""subscribers.py — 共用 watchlist.json：每位 LINE 使用者的追蹤清單 + 授權名單。

單一真相源（對齊使用者 spec）
------------------------------
一份 JSON 同時存兩件事，webhook（即時改）與排程 push（隔天讀）共用：

    {
      "users": { "<userId>": [ <WatchItem dict>, ... ], ... },   # 每人各自清單
      "allow": [ { "id": "<userId>", "name": "..." }, ... ]        # 管理員授權名單
    }

`SubscriberStore` 為介面，兩種後端同介面（上層不需知道存哪）：
* `JsonSubscriberStore`   — 本機檔（NAS webhook 單機常駐用），原子寫入。
* `GithubSubscriberStore` — GitHub repo 內 JSON（Contents API），雲端 dashboard / NAS / cron 共用。

向後相容：舊格式（頂層直接是 `{userId: [items]}`，無 users/allow 包一層）→ 讀取時
自動遷移為新格式（包進 users、allow 補空），寫回即升級，不需手動改檔。

Fail-Loud：檔案損毀 / 格式錯誤 → raise，不靜默吞。
"""

from __future__ import annotations

import json
import os
import tempfile

from .contracts import WatchItem
from .subscribers_core import (
    SubscriberStore,
    SubscriberStoreError,
    allow_ids_of,
    apply_grant,
    apply_revoke,
    format_allow_list,
    item_from_dict,
    item_to_dict,
    normalize_doc,
)


class JsonSubscriberStore:
    """以單一 JSON 檔儲存 `{"users":{...},"allow":[...]}`。原子寫入，壞檔即 raise。"""

    def __init__(self, path: str) -> None:
        self.path = path

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {"users": {}, "allow": []}
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise SubscriberStoreError(f"讀取訂閱檔失敗 {self.path}：{exc}") from exc
        return normalize_doc(data)

    def _save(self, doc: dict) -> None:
        # 原子寫入：先寫暫存再 replace，避免半寫壞檔。
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # 每人清單 ---------------------------------------------------------------
    def user_ids(self) -> list[str]:
        return list(self._load()["users"].keys())

    def get(self, user_id: str) -> list[WatchItem]:
        return [item_from_dict(d) for d in self._load()["users"].get(user_id, [])]

    def set(self, user_id: str, items: list[WatchItem]) -> None:
        if not user_id:
            raise ValueError("user_id 不可為空")
        doc = self._load()
        doc["users"][user_id] = [item_to_dict(it) for it in items]
        self._save(doc)

    def add_item(self, user_id: str, item: WatchItem) -> None:
        items = self.get(user_id)
        items = [it for it in items if it.tw_stock_id != item.tw_stock_id]  # 同代號視為更新
        items.append(item)
        self.set(user_id, items)

    def remove_item(self, user_id: str, tw_stock_id: str) -> bool:
        items = self.get(user_id)
        kept = [it for it in items if it.tw_stock_id != tw_stock_id]
        if len(kept) == len(items):
            return False
        self.set(user_id, kept)
        return True

    def remove_user(self, user_id: str) -> None:
        doc = self._load()
        if doc["users"].pop(user_id, None) is not None:
            self._save(doc)

    # 授權名單 ---------------------------------------------------------------
    def allow_ids(self) -> set[str]:
        return allow_ids_of(self._load()["allow"])

    def grant(self, user_id: str, name: str = "") -> tuple[bool, str]:
        doc = self._load()
        changed, msg = apply_grant(doc["allow"], user_id, name)
        if changed:
            self._save(doc)
        return changed, msg

    def revoke(self, user_id: str) -> tuple[bool, str]:
        doc = self._load()
        changed, msg = apply_revoke(doc["allow"], user_id)
        if changed:
            self._save(doc)
        return changed, msg

    def allow_text(self) -> str:
        return format_allow_list(self._load()["allow"])


def _resolve_github_token(env) -> str | None:
    """GitHub token：優先環境變數 GITHUB_TOKEN;否則讀 GITHUB_TOKEN_FILE 指向的檔
    （chmod 600、切勿進版控 —— 與 mynews NAS bot 同慣例）。檔案設了卻讀不到 → raise（不靜默退本機）。"""
    tok = env("GITHUB_TOKEN")
    if tok:
        return tok
    path = env("GITHUB_TOKEN_FILE")
    if path:
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as fh:
                return fh.read().strip() or None
        except OSError as exc:
            raise SubscriberStoreError(f"讀取 GITHUB_TOKEN_FILE 失敗 {path}：{exc}") from exc
    return None


def make_subscriber_store(*, get_env=None, local_path: str | None = None) -> SubscriberStore:
    """依環境變數選 backend（dashboard / cron / webhook 共用一個入口 → SSOT）。

    - SUBSCRIBERS_BACKEND=github（或未設但有 GITHUB_TOKEN[_FILE] + GITHUB_REPO）→ GithubSubscriberStore
      （雲端 + NAS 共用 repo 內 JSON）。用 SUBSCRIBERS_REPO_PATH（預設 subscribers.json）/ GITHUB_BRANCH。
      token 來源：GITHUB_TOKEN 環境變數，或 GITHUB_TOKEN_FILE 指向的檔（同 mynews NAS bot 慣例）。
    - 否則 → JsonSubscriberStore（本機檔，路徑 local_path > SUBSCRIBERS_FILE env > paths SSOT）。
      落地位置走 paths.SUBSCRIBERS_FILE（data/），不再散落 repo root（別亂放檔案）；
      既有 root subscribers.json 存在則向後相容沿用（見 _default_subscribers_path）。

    get_env 可注入（測試 / 讀 st.secrets 用），預設讀 os.environ。
    local_path 由 CLI flag（--subscribers / --store）傳入，優先於 SUBSCRIBERS_FILE。
    """
    env = get_env or os.environ.get
    backend = (env("SUBSCRIBERS_BACKEND") or "").strip().lower()
    token, repo = _resolve_github_token(env), env("GITHUB_REPO")
    use_github = backend == "github" or (not backend and token and repo)
    if use_github:
        from .github_store import GithubSubscriberStore  # lazy：僅選 github backend 時才載入

        return GithubSubscriberStore(
            token or "", repo or "",
            path=env("SUBSCRIBERS_REPO_PATH") or "subscribers.json",
            branch=env("GITHUB_BRANCH") or "main",
        )
    return JsonSubscriberStore(local_path or env("SUBSCRIBERS_FILE") or _default_subscribers_path())


def _default_subscribers_path() -> str:
    """本機 subscribers 落地預設（SSOT: paths.SUBSCRIBERS_FILE = data/）。

    別亂放檔案：不再落 repo root 的 bare `subscribers.json`。但既有部署可能已有一顆
    root subscribers.json（gitignore 的 per-machine 檔,含真實 userId）→ 存在則沿用不孤兒化。
    """
    from paths import LEGACY_SUBSCRIBERS_FILE, SUBSCRIBERS_FILE

    return LEGACY_SUBSCRIBERS_FILE if os.path.exists(LEGACY_SUBSCRIBERS_FILE) else SUBSCRIBERS_FILE


def store_is_github(*, get_env=None) -> bool:
    """上層（dashboard）判斷是否已設定 GitHub 持久化（用來決定要不要顯示『存得住』）。"""
    env = get_env or os.environ.get
    backend = (env("SUBSCRIBERS_BACKEND") or "").strip().lower()
    has_token = bool(env("GITHUB_TOKEN")) or bool(env("GITHUB_TOKEN_FILE"))
    return backend == "github" or (not backend and has_token and bool(env("GITHUB_REPO")))
