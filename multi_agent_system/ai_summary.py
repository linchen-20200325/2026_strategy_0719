"""ai_summary.py — 個股新聞的 AI 總結（Gemini，標準庫 urllib，無第三方相依）。

用途
----
把 news.db 撈到的某檔近日新聞，用 Gemini 濃縮成 2-3 句繁中重點（供每日盯盤卡）。
純顯示用途 —— 對照使用者 Fund 專案 EX-AI-1 原則：**LLM 輸出只當字串顯示，
嚴禁從中萃取數字回填為 data**（財報數字一律走 stock.db，不靠 AI）。

設定（環境變數）
----------------
    GEMINI_API_KEY    單把 key；或用「逗號 / 空白」分隔放**多把**（會輪替 + 失敗自動換把）。
    GEMINI_API_KEYS   多把 key（逗號 / 空白分隔），**優先於** GEMINI_API_KEY。
    GEMINI_MODEL      選用模型，預設 gemini-2.5-flash。

多把 key 策略（免費額度分流）
------------------------------
* **輪替（round-robin）**：每次總結從下一把 key 起用 → 把每分鐘請求數攤平到 N 把，降低單把限流。
* **失敗換把（failover）**：某把回 429 / 連線錯 → 立刻改用下一把，全部失敗才放棄。
* log **只印第幾把 / 共幾把**，永不印 key 本身。

失敗降級（不拖垮推播）
----------------------
無 key / 無新聞 / 全部 key 失敗 → 回 None；caller（盯盤卡）自動退成「列出頭條標題」，
誠實不杜撰（§1 Fail Loud：AI 掛了就不要 AI，不要編一段假總結）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Sequence

from .contracts import NewsItem

logger = logging.getLogger("multi_agent_system.ai")

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_DEFAULT_MODEL = "gemini-2.5-flash"
_MAX_NEWS = 8          # 最多餵幾則（控 token）
_MAX_SUMMARY_LEN = 300  # 個股新聞總結截斷（盯盤卡一行不宜過長）
_MAX_READ_LEN = 500     # 市場綜合解讀截斷（3-5 句）

# 輪替起點（process-local；GitHub Actions 每次 run 為全新 process → 自然歸零）。
_rr_offset = 0


def _resolve_keys(api_key, api_keys, get_env) -> list[str]:
    """彙整可用 key 清單（去重、保序）。優先序：顯式 api_keys > api_key > env。

    字串會以「逗號 / 空白」切成多把（Gemini key 不含這些字元，切法安全）。
    """
    if api_keys is not None:
        raw = api_keys
    elif api_key is not None:
        raw = api_key
    else:
        raw = get_env("GEMINI_API_KEYS") or get_env("GEMINI_API_KEY")
    if raw is None:
        return []
    parts = re.split(r"[,\s]+", raw.strip()) if isinstance(raw, str) else list(raw)
    seen: set[str] = set()
    keys: list[str] = []
    for p in parts:
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            keys.append(p)
    return keys


def _build_prompt(stock_id: str, items: Sequence[NewsItem]) -> str:
    lines = [
        f"你是台股投研助理。以下是「{stock_id}」近日的新聞標題，附情緒分數（-1 極空 ~ +1 極多）。",
        "請用繁體中文寫 2-3 句重點總結：市場在關注什麼、整體偏多還偏空。",
        "只依據下列新聞，不要杜撰任何數字或事實，不要加開場白與結尾客套。",
        "",
        "新聞：",
    ]
    for n in items[:_MAX_NEWS]:
        lines.append(f"- [{n.sentiment_score:+.2f}] {n.title}")
    return "\n".join(lines)


def _extract_text(payload: dict) -> str | None:
    """從 Gemini 回應取出第一段文字；結構不符 → None（不硬湊）。"""
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except (KeyError, IndexError, TypeError):
        return None


def _call_once(key: str, model: str, prompt: str, timeout: float) -> tuple[str | None, bool]:
    """打一次 Gemini。回 (文字或 None, 是否該換下一把 key)。

    * 200 + 有文字 → (文字, False)
    * 200 但無文字 → (None, False)  （回應正常但空，換 key 也一樣 → 不換）
    * 連線 / HTTP 錯（含 429 限流）→ (None, True)  （這把不行，換下一把）
    """
    url = _GEMINI_URL.format(model=model) + f"?key={key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 256},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError):
        # HTTPError 亦為 URLError 子類（含 429/5xx/4xx）→ 一律換下一把。
        return None, True
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None, True
    return _extract_text(payload), False


def _generate(
    prompt: str,
    *,
    api_key: str | None,
    api_keys: list[str] | str | None,
    model: str | None,
    timeout: float,
    get_env,
    label: str,
    max_len: int,
) -> str | None:
    """打 Gemini（多把 key 輪替 + 失敗自動換把）。無 key / 空 prompt / 全部失敗 → None。

    共用給「個股新聞總結」與「市場綜合解讀」——key 輪替邏輯 SSOT，不重複。
    """
    global _rr_offset
    keys = _resolve_keys(api_key, api_keys, get_env)
    if not keys or not prompt.strip():
        return None
    mdl = model or get_env("GEMINI_MODEL") or _DEFAULT_MODEL
    start = _rr_offset % len(keys)   # 本次從第 start 把起
    _rr_offset += 1                  # 下次換下一把起（攤平負載）
    for i in range(len(keys)):
        idx = (start + i) % len(keys)
        text, try_next = _call_once(keys[idx], mdl, prompt, timeout)
        if text is not None:
            return text[:max_len]
        if not try_next:
            return None              # 回應正常但空 → 換 key 無益
        logger.warning("Gemini 第 %d/%d 把 key 失敗（%s）→ 換下一把", idx + 1, len(keys), label)
    logger.warning("Gemini %d 把 key 全數失敗（%s）", len(keys), label)
    return None


def summarize_stock_news(
    stock_id: str,
    items: Sequence[NewsItem],
    *,
    api_key: str | None = None,
    api_keys: list[str] | str | None = None,
    model: str | None = None,
    timeout: float = 20.0,
    get_env=os.environ.get,
) -> str | None:
    """一檔的新聞 → AI 2-3 句繁中總結。無 key / 無新聞 / 全部失敗 → None（caller 退標題）。"""
    if not items:
        return None
    return _generate(
        _build_prompt(stock_id, items),
        api_key=api_key, api_keys=api_keys, model=model, timeout=timeout,
        get_env=get_env, label=stock_id, max_len=_MAX_SUMMARY_LEN,
    )


def _build_market_prompt(facts: str) -> str:
    return (
        "你是投研助理。下面是今天的市場數據（數字皆為實測，禁止更改或杜撰）：\n\n"
        f"{facts}\n\n"
        "請用繁體中文寫 3-5 句『綜合解讀』：目前國際與台股大局偏多還偏空、關鍵驅動因子、"
        "值得留意的風險。只根據上列數據解讀，不要杜撰任何數字，不要開場白與客套。"
    )


def interpret_market(
    facts: str,
    *,
    api_key: str | None = None,
    api_keys: list[str] | str | None = None,
    model: str | None = None,
    timeout: float = 25.0,
    get_env=os.environ.get,
) -> str | None:
    """市場數據 facts（已組好的快訊文字）→ AI 3-5 句綜合解讀。

    對照 §3b / Fund EX-AI-1：AI 只**解讀**（出文字），數字一律由 facts（規則式+DB）提供，
    不從 AI 萃取。無 key / 空 / 失敗 → None（caller 誠實不顯示 AI 解讀，不杜撰）。
    """
    if not facts or not facts.strip():
        return None
    return _generate(
        _build_market_prompt(facts),
        api_key=api_key, api_keys=api_keys, model=model, timeout=timeout,
        get_env=get_env, label="market", max_len=_MAX_READ_LEN,
    )
