#!/usr/bin/env python3
"""nas_line_bot.py — 常駐 LINE webhook:讓好友在 LINE 上自選要盯的標的（加/刪/清單）。

為什麼要這支
------------
整套推播是「單向排程」（cron 讀 subscribers.json → 逐人 push），沒有能「接收」LINE
訊息的伺服器。要讓好友自己加/刪標的、並取得自己的 userId，必須有一台常駐在線的程式接
LINE webhook —— 這支就跑在 24h 開機的 NAS 上（與 run_pipeline.py 的 cron 同一台），
收到訊息就改**同一個** subscribers.json，排程端下一輪就讀到。

資料流
------
    好友在 LINE 打「加 2330」
      → LINE 平台 POST /callback
      → 驗簽（HMAC-SHA256，用 channel secret）
      → 解析指令 → JsonSubscriberStore.add_item / remove_item 寫本機 subscribers.json
      → reply 回目前清單
    canonical 清單 = 本機 subscribers.json；排程端 run_pipeline.py --per-user 讀同一份。

與 mynews/scripts/nas_line_bot.py 的差異（SSOT）
----------------------------------------------
mynews 那支在 NAS 單檔常駐、刻意零相依，故「就地內嵌 store 邏輯 + 寫回 GitHub」。
本專案的 NAS 本來就有整包 repo（cron 要跑整個 multi_agent_system + 三個 DB），
所以這支**直接 import 專案的 JsonSubscriberStore / WatchItem，寫本機同一個檔** ——
不必維護兩份 store 邏輯、也不需 GitHub API。若未來要把 bot 與排程拆到不同機器，
再改成共享儲存（DB / GitHub 寫回）即可，屆時只換 store 實作、上層不動。

指令
----
    id / 我的id                回你的 userId（貼給管理員授權）
    加 2330 台積電             加一檔（可帶名稱→存為新聞關鍵字）；加 ETF 0050 / 加 基金 <code>
    刪 2330                     移除一檔
    清單                        列出你目前盯的清單
    授權 <userId> [名字]        （管理員）開通某人可用
    撤銷 <userId>               （管理員）撤銷授權
    名單                        （管理員）列出授權名單

設定（環境變數；切勿寫進程式或進版控）
--------------------------------------
    LINE_CHANNEL_ACCESS_TOKEN   推播用的 channel access token（本 bot reply 預設沿用同一個 OA）
    LINE_CHANNEL_SECRET         同一 channel 的 secret（驗 X-Line-Signature，必填）
    STRATEGY_BOT_TOKEN          （選）另設一個 OA 當 bot 時用它蓋掉 reply token
    STRATEGY_BOT_PORT           監聽埠，預設 8090（避開 mynews bot 的 8080）
    SUBSCRIBERS_FILE            清單路徑（本機後端），預設 subscribers.json（與 run_pipeline --subscribers 對齊）
    GITHUB_TOKEN / GITHUB_TOKEN_FILE + GITHUB_REPO
                                （選）改用 GitHub 共用 JSON 後端；清單與**授權名單（allow）存同一份**
    STRATEGY_ALLOW_USER         （選）bootstrap 授權名單，逗號/空白分隔；留空=不限制（對外開放）
    STRATEGY_ADMIN_USER         （選）管理員 userId；未設則沿用 STRATEGY_ALLOW_USER

啟動
----
    LINE_CHANNEL_ACCESS_TOKEN=xxx LINE_CHANNEL_SECRET=yyy python scripts/nas_line_bot.py
LINE Developers Console → Messaging API → Webhook URL 填 https://<你的網域>/callback，
並開啟「Use webhook」。詳見 deploy/nas_line_bot.md。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 入口腳本：讓 `python scripts/nas_line_bot.py` 從 repo 根找得到 multi_agent_system。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_WEIGHT_RATIO  # noqa: E402
from multi_agent_system.contracts import WatchItem  # noqa: E402
from multi_agent_system.infra.http import HttpError, request_json  # noqa: E402
from multi_agent_system.line_push import MAX_LINE_TEXT_LEN  # noqa: E402
from multi_agent_system.subscribers import (  # noqa: E402
    SubscriberStore,
    SubscriberStoreError,
    make_subscriber_store,
)

logger = logging.getLogger("multi_agent_system.bot")

LINE_REPLY_ENDPOINT = "https://api.line.me/v2/bot/message/reply"

# ── 指令關鍵字（純解析，無 I/O，可單測）─────────────────────────────────────
_TICKER_RE = re.compile(r"[0-9]{4,6}[A-Z]?")     # 台股 / ETF 代號:4~6 碼,可帶單一英文尾
_ADD_KW = ("新增", "加入", "加", "add", "+")
_DEL_KW = ("刪除", "移除", "刪", "remove", "del", "-")
_LIST_KW = ("清單", "清单", "list", "ls")
_ID_KW = ("id", "我的id", "myid")
# 類別關鍵字 → 正規化標籤（WatchItem.category）
_CATEGORY = {"台股": "台股", "股票": "台股", "股": "台股",
             "etf": "ETF", "ｅｔｆ": "ETF",
             "基金": "基金", "fund": "基金"}

# ── 管理員指令關鍵字 ─────────────────────────────────────────────────────────
_GRANT_KW = ("授權", "允許")
_REVOKE_KW = ("撤銷", "取消授權", "解除授權")
_ALLOWLIST_TEXT = ("名單", "授權名單", "授權清單")


def normalize_ticker(raw: str) -> str:
    """抓出數字代號（台股/ETF）。基金若為英數代號則另走 parse_add 的 fallback。"""
    m = _TICKER_RE.search((raw or "").upper())
    return m.group(0) if m else ""


def parse_command(text: str) -> tuple[str, str]:
    """text → (action, arg)；action ∈ {add, remove, list, id, help}。"""
    t = (text or "").strip()
    low = t.lower()
    if low in _ID_KW:
        return "id", ""
    for kw in _ADD_KW:
        if low.startswith(kw.lower()):
            return "add", t[len(kw):].strip()
    for kw in _DEL_KW:
        if low.startswith(kw.lower()):
            return "remove", t[len(kw):].strip()
    if low in _LIST_KW:
        return "list", ""
    return "help", t


def parse_add(arg: str) -> tuple[str, str, str]:
    """「[類別] 代號 [名稱]」→ (category, code, name)。code 空 = 看不懂。"""
    parts = (arg or "").strip().split()
    if not parts:
        return "台股", "", ""
    category = "台股"
    if parts and parts[0].lower() in _CATEGORY:
        category = _CATEGORY[parts[0].lower()]
        parts = parts[1:]
    if not parts:
        return category, "", ""
    token, name = parts[0], " ".join(parts[1:]).strip()
    if category == "基金":
        # 基金代號可能含英文 → 取原 token（去空白）大寫;非純數字亦接受。
        code = token.upper()
    else:
        code = normalize_ticker(token)
    return category, code, name


def parse_admin(text: str) -> tuple[str, str]:
    """→ (action, arg)；action ∈ {grant, revoke, allowlist, ''}。"""
    t = (text or "").strip()
    if t in _ALLOWLIST_TEXT:                       # 先比完整詞,避免「授權名單」被「授權」吃掉
        return "allowlist", ""
    low = t.lower()
    for kw in _GRANT_KW:
        if low.startswith(kw.lower()):
            return "grant", t[len(kw):].strip()
    for kw in _REVOKE_KW:
        if low.startswith(kw.lower()):
            return "revoke", t[len(kw):].strip()
    return "", t


def _split_id_name(arg: str) -> tuple[str, str]:
    parts = (arg or "").split(None, 1)
    if not parts:
        return "", ""
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def _mask_uid(user_id: str) -> str:
    """log 用：只留前 8 碼（避免完整 userId 進日誌，對照 spec 安全要求）。"""
    return (user_id[:8] + "…") if user_id else "?"


# ── 授權名單（存共用 watchlist.json 的 allow 欄位；管理員用 LINE 加人免重啟）──────
def _env_set(name: str) -> set[str]:
    return {u for u in re.split(r"[,\s]+", os.environ.get(name, "")) if u}


def effective_allowed(store: SubscriberStore) -> set[str]:
    """有效授權集合 = 共用 watchlist.json 的 allow 名單 ∪ env STRATEGY_ALLOW_USER（bootstrap）。"""
    return store.allow_ids() | _env_set("STRATEGY_ALLOW_USER")


def is_user_allowed(store: SubscriberStore, user_id: str) -> bool:
    """可用『加/刪/清單』者;有效集合為空 = 不限制（對外開放）。"""
    eff = effective_allowed(store)
    return (not eff) or (user_id in eff)


def admin_ids() -> set[str]:
    return _env_set("STRATEGY_ADMIN_USER") or _env_set("STRATEGY_ALLOW_USER")


def is_admin(user_id: str) -> bool:
    return bool(user_id) and user_id in admin_ids()


# ── 清單呈現 ─────────────────────────────────────────────────────────────────
def format_list_for(store: SubscriberStore, user_id: str) -> str:
    items = store.get(user_id)
    if not items:
        return "你的盯盤清單是空的。傳「加 2330」加入第一檔。"
    lines = [f"📋 你的盯盤清單（{len(items)} 檔）："]
    for it in items:
        kw = f"〔{'、'.join(it.keywords)}〕" if it.keywords else ""
        lines.append(f"・{it.category} {it.tw_stock_id}{('  ' + kw) if kw else ''}")
    lines.append("")
    lines.append("指令：加 2330 台積電 / 刪 2330 / 清單")
    return "\n".join(lines)


def help_text() -> str:
    return (
        "投研盯盤指令：\n"
        "・加 2330 台積電（加入；名稱會當新聞關鍵字）\n"
        "・加 ETF 0050 / 加 基金 <代號>\n"
        "・刪 2330（移除）\n"
        "・清單（列出你的清單）\n"
        "・id（拿你的 userId 給管理員授權）\n\n"
        "每天早盤前 / 收盤後會推你清單內標的的『利多』訊號。"
    )


def handle_text(
    text: str,
    user_id: str,
    *,
    store: SubscriberStore,
) -> str:
    """一則文字訊息 → 回覆字串。store 注入,方便單測。授權名單也走同一個 store。"""
    low = (text or "").strip().lower()
    # 「id」任何人都回（新朋友自助取得 userId，貼給管理員授權）；不需被授權。
    if low in _ID_KW:
        return (f"你的 userId：\n{user_id}\n"
                "（把這串貼給管理員,他用「授權 這串」開通你;"
                "開通後傳「加 2330」建立你的專屬清單）")

    # 管理員指令：授權 / 撤銷 / 名單（寫共用 watchlist.json 的 allow，即時生效免重啟）
    admin_action, admin_arg = parse_admin(text)
    if admin_action in ("grant", "revoke", "allowlist"):
        if not is_admin(user_id):
            return "（這是管理員指令,你沒有權限。需要的話請管理員幫你操作。）"
        try:
            if admin_action == "allowlist":
                return store.allow_text()
            if admin_action == "grant":
                uid, name = _split_id_name(admin_arg)
                _, msg = store.grant(uid, name)
            else:
                _, msg = store.revoke(admin_arg.strip())
            return msg + "\n\n" + store.allow_text()
        except (SubscriberStoreError, OSError) as exc:
            logger.error("授權名單寫入失敗：%s", exc)
            return "授權名單更新失敗（寫入出錯）,請稍後再試。"

    # 一般指令（加/刪/清單）：需被授權
    if not is_user_allowed(store, user_id):
        return ("你還沒被授權使用 🙅\n"
                "把下面這串你的 userId 貼給管理員,請他用「授權 這串」開通：\n"
                f"{user_id}")

    action, arg = parse_command(text)
    if action == "list":
        return format_list_for(store, user_id)
    if action == "add":
        category, code, name = parse_add(arg)
        if not code:
            return f"看不懂代號「{arg}」。台股/ETF 給 4~6 位數字（例：加 2330 台積電）。"
        keywords = (name,) if name else ()
        try:
            # 權重預設走 config SSOT；max_weight_ratio 用 WatchItem 的 config-backed 預設。
            store.add_item(user_id, WatchItem(
                tw_stock_id=code, us_stock_id="", keywords=keywords,
                current_weight_ratio=DEFAULT_WEIGHT_RATIO,
                sharpe=None, category=category,
            ))
        except (SubscriberStoreError, OSError) as exc:
            logger.error("寫清單失敗 uid=%s：%s", user_id, exc)
            return "清單更新失敗（寫檔出錯）,請稍後再試。"
        return f"✅ 已加入 {category} {code}{('  ' + name) if name else ''}。\n\n" + \
            format_list_for(store, user_id)
    if action == "remove":
        code = normalize_ticker(arg) or arg.strip().upper()
        try:
            removed = store.remove_item(user_id, code)
        except (SubscriberStoreError, OSError) as exc:
            logger.error("移除清單失敗 uid=%s：%s", user_id, exc)
            return "清單更新失敗（寫檔出錯）,請稍後再試。"
        if not removed:
            return f"{code} 不在你的清單內。"
        return f"🗑️ 已移除 {code}。\n\n" + format_list_for(store, user_id)
    return help_text()


# ── LINE reply + 驗簽（I/O）──────────────────────────────────────────────────
def verify_signature(secret: str, body: bytes, header: str) -> bool:
    """LINE X-Line-Signature = base64(HMAC-SHA256(secret, rawBody))。"""
    if not secret:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, header or "")


def _reply_token() -> str:
    return (os.environ.get("STRATEGY_BOT_TOKEN")
            or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")).strip()


def line_reply(reply_token: str, text: str) -> None:
    # webhook reply：失敗只 warn 不 raise（避免回 500 給 LINE 觸發重試風暴）。
    try:
        status, _ = request_json(
            "POST", LINE_REPLY_ENDPOINT,
            body={
                "replyToken": reply_token,
                "messages": [{"type": "text", "text": text[:MAX_LINE_TEXT_LEN]}],
            },
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {_reply_token()}"},
            timeout=30,
        )
        if status != 200:
            logger.warning("LINE reply 非 200：%s", status)
    except HttpError as exc:
        logger.warning("LINE reply 失敗：%s", exc)


def _store() -> SubscriberStore:
    # 依環境變數選 backend：設 GITHUB_TOKEN + GITHUB_REPO → 寫共享 repo JSON（雲端 dashboard 也看得到）。
    # 清單與授權名單（allow）都在這同一份 JSON。
    return make_subscriber_store()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:   # 靜音預設 access log（改用 logger）
        pass

    def do_GET(self) -> None:                # 健檢：瀏覽器/監控打一下回 200
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"strategy watch bot ok")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        # 先回 200 給 LINE（避免重送）；驗簽不過則不處理事件。
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
        secret = os.environ.get("LINE_CHANNEL_SECRET", "")
        if not verify_signature(secret, body, self.headers.get("X-Line-Signature", "")):
            logger.warning("簽章驗證失敗,忽略此次 webhook")
            return
        try:
            events = json.loads(body or b"{}").get("events", [])
        except (json.JSONDecodeError, ValueError, AttributeError) as exc:
            # 驗簽已過但 body 非合法 JSON 物件 → 不靜默丟棄,留 log 供排查（可觀測性）。
            logger.warning("webhook body 非合法 JSON 物件,忽略（%d bytes）：%s", len(body), exc)
            return
        store = _store()
        for ev in events:
            if ev.get("type") != "message" or ev.get("message", {}).get("type") != "text":
                continue
            user_id = ev.get("source", {}).get("userId", "")
            text = ev.get("message", {}).get("text", "")
            logger.info("收到訊息 userId=%s：%r", _mask_uid(user_id), text)
            try:
                reply = handle_text(text, user_id, store=store)
            except Exception as exc:  # noqa: BLE001 — 單則失敗不該拖垮服務
                logger.error("處理訊息例外：%s", exc)
                reply = "處理時發生錯誤,請稍後再試。"
            if ev.get("replyToken"):
                line_reply(ev["replyToken"], reply)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not _reply_token():
        logger.error("未設定 LINE_CHANNEL_ACCESS_TOKEN（或 STRATEGY_BOT_TOKEN）→ 無法 reply")
        return 2
    if not os.environ.get("LINE_CHANNEL_SECRET"):
        logger.error("未設定 LINE_CHANNEL_SECRET → 無法驗簽,拒絕啟動（Fail-Loud）")
        return 2
    port = int(os.environ.get("STRATEGY_BOT_PORT", "8090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    logger.info("投研盯盤 webhook 啟動,監聽 :%d（LINE Webhook URL 指向本機 /callback）", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("收到中斷,關閉服務")
    return 0


if __name__ == "__main__":
    sys.exit(main())
