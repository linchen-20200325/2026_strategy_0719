"""ai_summary.py — 個股新聞的 AI 總結（Gemini，標準庫 urllib，無第三方相依）。

用途
----
把 news.db 撈到的某檔近日新聞，用 Gemini 濃縮成 2-3 句繁中重點（供每日盯盤卡）。
純顯示用途 —— 對照使用者 Fund 專案 EX-AI-1 原則：**LLM 輸出只當字串顯示，
嚴禁從中萃取數字回填為 data**（財報數字一律走 stock.db，不靠 AI）。

設定（環境變數）
----------------
    GEMINI_API_KEY   Google AI Studio 的 API key（未設 → 不呼叫，回 None，caller 退標題）。
    GEMINI_MODEL     選用模型，預設 gemini-2.5-flash。

失敗降級（不拖垮推播）
----------------------
無 key / 無新聞 / API 失敗 → 回 None；caller（盯盤卡）自動退成「列出頭條標題」，
誠實不杜撰（§1 Fail Loud：AI 掛了就不要 AI，不要編一段假總結）。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Sequence

from .contracts import NewsItem

logger = logging.getLogger("multi_agent_system.ai")

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_DEFAULT_MODEL = "gemini-2.5-flash"
_MAX_NEWS = 8          # 最多餵幾則（控 token）
_MAX_SUMMARY_LEN = 300  # 回傳截斷（盯盤卡一行不宜過長）


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


def summarize_stock_news(
    stock_id: str,
    items: Sequence[NewsItem],
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = 20.0,
) -> str | None:
    """一檔的新聞 → AI 2-3 句繁中總結。無 key / 無新聞 / 失敗 → None（caller 退標題）。"""
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key or not items:
        return None
    mdl = model or os.environ.get("GEMINI_MODEL") or _DEFAULT_MODEL
    url = _GEMINI_URL.format(model=mdl) + f"?key={key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": _build_prompt(stock_id, items)}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 256},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        logger.warning("Gemini 總結失敗 %s：%s → 退回頭條標題", stock_id, exc)
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Gemini 回應非 JSON（%s）→ 退回頭條標題", stock_id)
        return None
    text = _extract_text(payload)
    if text is None:
        logger.warning("Gemini 回應無文字（%s）→ 退回頭條標題", stock_id)
        return None
    return text[:_MAX_SUMMARY_LEN]
