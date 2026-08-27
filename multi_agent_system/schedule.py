"""schedule.py — 排程場次判定與「遲到」量測（L0，純函式、零 I/O）。

單一職責：把「**實際觸發的那條 cron 字串**」翻成場次（morning / afternoon），
並算出這次執行比排定時刻晚了多久。

為什麼不看時鐘（本模組存在的理由）
--------------------------------
GitHub Actions 的 `schedule` 只保證「不早於」排定時刻，實務上常延遲數小時。
2026-08-27 盤前班（cron `30 23 * * 0-4`，TW 07:30）被延遲 5h09m 才啟動，
原本 workflow 用 `date -u +%-H` 反推場次 → 04:39 UTC 落進「其餘 → afternoon」，
於是 12:39 TW（台股 13:30 才收盤）推出一則標「收盤後」的快訊。
**排定時刻是已知事實，執行時刻不是** —— 故一律以 cron 原文為準。

判定優先序（沒有 else，查不到就 Fail-Loud）
-----------------------------------------
1. 明示 session（CLI `--session`）—— NAS crontab / workflow_dispatch 走這條。
2. `github.event.schedule` 的 cron 原文 → 查 `config.CRON_SESSIONS`。
3. 皆無 / cron 未登錄 → `ScheduleError`（呼叫端須大聲報錯 + 非零退出，不猜）。

時間注入
--------
所有需要「現在」的函式一律以 `now_utc` **參數注入**（L0 無 I/O）；
呼叫端才去取時鐘。這也讓事故當下的時刻能被測試逐分鐘重現。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import CRON_SESSIONS, SESSION_MAX_DELAY_MIN

__all__ = [
    "ScheduleError",
    "is_too_late",
    "lateness",
    "resolve_session",
    "scheduled_fire_utc",
]


class ScheduleError(ValueError):
    """場次 / 排程時刻判不出來。呼叫端應大聲報錯並以非零碼退出（絕不 fallback 猜）。"""


def _normalize_cron(cron: str) -> str:
    """把 cron 原文收斂成 `config.CRON_SESSIONS` 的 key 形式（欄位間單一空白）。

    YAML 端的縮排 / 多重空白不該影響比對，但**欄位內容**一字不改（不做語意等價展開：
    `30 8 * * 1-5` 與 `30 08 * * 1-5` 視為不同 key —— 要能對上就把 YAML 寫成登錄的原文，
    這正是「YAML 與 config 綁成同一份真相」的用意）。
    """
    return " ".join(cron.split())


def resolve_session(*, explicit: str | None = None, cron: str | None = None) -> str:
    """決定本次執行的場次。優先序：明示 > cron 對照表 > Fail-Loud。

    explicit：CLI `--session` 的值（None / 空字串視為未提供）。
    cron：`github.event.schedule` 的 cron 原文（None / 空字串視為未提供）。
    """
    if explicit:
        if explicit not in SESSION_MAX_DELAY_MIN:
            raise ScheduleError(
                f"未知場次 {explicit!r}；已登錄：{sorted(SESSION_MAX_DELAY_MIN)}"
            )
        return explicit
    if cron:
        key = _normalize_cron(cron)
        if key in CRON_SESSIONS:
            return CRON_SESSIONS[key]
        raise ScheduleError(
            f"cron {key!r} 未登錄於 config.CRON_SESSIONS（已登錄：{sorted(CRON_SESSIONS)}）；"
            "新增排程時必須同步登錄，本系統不以時鐘反推場次"
        )
    raise ScheduleError(
        "無法判定場次：既未給 --session，也沒有 github.event.schedule 的 cron 原文。"
        "依 Fail-Loud 原則不猜場次（猜錯會把盤前計畫標成收盤後快訊）"
    )


def _fire_hour_minute(cron: str) -> tuple[int, int]:
    """從 cron 原文取排定的「時、分」（UTC）。

    刻意**不另建一份 cron→時分對照表**：cron 字串本身就帶著時分，另存一份就是兩個
    可各自漂移的真相源（違反 SSOT）。只支援純數字的分 / 時欄位 —— 本系統的排程都是
    定點觸發；遇到 `*/5`、`1,31` 這類無法對應單一排定時刻的寫法一律 Fail-Loud。
    """
    fields = _normalize_cron(cron).split(" ")
    if len(fields) != 5:
        raise ScheduleError(f"cron {cron!r} 欄位數不是 5")
    minute_s, hour_s = fields[0], fields[1]
    if not (minute_s.isdigit() and hour_s.isdigit()):
        raise ScheduleError(
            f"cron {cron!r} 的分 / 時欄位不是純數字（{minute_s!r} {hour_s!r}），無法對應單一排定時刻"
        )
    minute, hour = int(minute_s), int(hour_s)
    if not (0 <= minute <= 59 and 0 <= hour <= 23):
        raise ScheduleError(f"cron {cron!r} 的分 / 時超出範圍")
    return hour, minute


def _as_utc(now_utc: datetime) -> datetime:
    """要求 tz-aware 並換算到 UTC。naive datetime 一律拒收（時區靜默錯位正是本次事故的近親）。"""
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ScheduleError("now_utc 必須是 tz-aware datetime（naive 會靜默錯位 8 小時）")
    return now_utc.astimezone(timezone.utc)


def scheduled_fire_utc(cron: str, now_utc: datetime) -> datetime:
    """本次執行**應該**被觸發的 UTC 時刻 ＝ now 之前（含）最近一次符合該時分的時刻。

    只接受已登錄於 `config.CRON_SESSIONS` 的 cron（未登錄 → ScheduleError），
    確保「能算遲到」與「能判場次」是同一組真相。

    ⚠️ 已知界線：**只比對時分、不比對星期 / 日期欄位**，故回推結果與 now 相差恆 < 24h，
    延遲超過 24h 會被低估。GitHub 的排程積壓不會到這個量級（且 24h 早已遠超遲到門檻，
    真發生時仍會被閘擋下），為此去實作完整 cron 展開屬過度設計 —— 先不做。
    """
    key = _normalize_cron(cron)
    if key not in CRON_SESSIONS:
        raise ScheduleError(
            f"cron {key!r} 未登錄於 config.CRON_SESSIONS，無法判定排定時刻"
        )
    hour, minute = _fire_hour_minute(key)
    now = _as_utc(now_utc)
    fired = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if fired > now:
        fired -= timedelta(days=1)   # 排定時刻在「今天」還沒到 → 這班是昨天那一輪（跨午夜）
    return fired


def lateness(cron: str, now_utc: datetime) -> timedelta:
    """這次執行比排定時刻晚了多久（>= 0）。"""
    delay = _as_utc(now_utc) - scheduled_fire_utc(cron, now_utc)
    # 由 scheduled_fire_utc 的回推方式，delay 結構上不可能為負；此處 clamp 是防呆，
    # 免得未來改回推邏輯時把負值當「早到」往下傳。
    return delay if delay > timedelta(0) else timedelta(0)


def is_too_late(session: str, delay: timedelta) -> bool:
    """該場次是否已逾時效（超過 `config.SESSION_MAX_DELAY_MIN`）。

    逾時的處置在呼叫端：不推播、不落帳、退出碼 0（平台延遲不是本系統故障，
    不該把 workflow 變紅），但要用 error 級別留下痕跡。
    """
    if session not in SESSION_MAX_DELAY_MIN:
        raise ScheduleError(
            f"未知場次 {session!r}，無遲到門檻可比對"
        )
    return delay > timedelta(minutes=SESSION_MAX_DELAY_MIN[session])
