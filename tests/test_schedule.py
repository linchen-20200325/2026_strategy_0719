"""test_schedule.py — 排程場次判定 + 遲到閘（`multi_agent_system.schedule`）迴歸。

守住 2026-08-27 的事故：盤前班（cron `30 23 * * 0-4`）被 GitHub 延遲 5h09m，
舊 workflow 以 `date -u +%-H` 反推場次 → 04:39 UTC 落進 else → 標成 afternoon，
於是 12:39 TW（台股 13:30 才收盤）推出一則「收盤後」快訊。
本檔把「場次只由 cron 原文決定、判不出就 Fail-Loud、遲到就不推」釘死。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config import CRON_SESSIONS, SESSION_LABELS, SESSION_MAX_DELAY_MIN
from multi_agent_system import schedule as sch
from multi_agent_system.schedule import (
    ScheduleError,
    is_too_late,
    lateness,
    resolve_session,
    scheduled_fire_utc,
)

MORNING_CRON = "30 23 * * 0-4"
AFTERNOON_CRON = "30 8 * * 1-5"
# 事故當下：盤前班實際啟動於 2026-08-27 04:39 UTC（＝ TW 12:39）。
INCIDENT_NOW = datetime(2026, 8, 27, 4, 39, tzinfo=timezone.utc)


# ------------------------------------------------------------------ 場次判定
def test_late_morning_run_keeps_morning_label():
    """★ 主戰場：延遲 5 小時的盤前班，場次仍是 morning（舊時鐘反推會給 afternoon）。"""
    assert resolve_session(explicit=None, cron=MORNING_CRON) == "morning"


def test_afternoon_cron_still_resolves_afternoon():
    """收盤後班不可被修正誤傷。"""
    assert resolve_session(explicit=None, cron=AFTERNOON_CRON) == "afternoon"


def test_explicit_session_wins_over_cron():
    """明示 --session（NAS crontab / workflow_dispatch）優先於 cron 對照表。"""
    assert resolve_session(explicit="afternoon", cron=MORNING_CRON) == "afternoon"


def test_unregistered_cron_raises():
    """未登錄的 cron → Fail-Loud，不得 fallback 回時鐘猜（沒有 else）。"""
    with pytest.raises(ScheduleError) as exc:
        resolve_session(explicit=None, cron="0 12 * * *")
    assert "CRON_SESSIONS" in str(exc.value)


def test_no_explicit_no_cron_raises():
    with pytest.raises(ScheduleError):
        resolve_session(explicit=None, cron=None)


def test_empty_strings_are_treated_as_absent():
    """`github.event.schedule` 在非排程觸發時是空字串 → 視為未提供，不是 key 查詢失敗。"""
    with pytest.raises(ScheduleError):
        resolve_session(explicit="", cron="")


def test_unknown_explicit_session_raises():
    with pytest.raises(ScheduleError):
        resolve_session(explicit="midnight", cron=None)


def test_cron_whitespace_is_normalized():
    assert resolve_session(explicit=None, cron="  30   23  *  *  0-4 ") == "morning"


# ------------------------------------------------------------------ 排定時刻 / 遲到
def test_scheduled_fire_walks_back_across_midnight():
    """23:30 的班在 04:39 執行 → 排定時刻是**前一天**的 23:30（跨午夜回推）。"""
    assert scheduled_fire_utc(MORNING_CRON, INCIDENT_NOW) == datetime(
        2026, 8, 26, 23, 30, tzinfo=timezone.utc
    )


def test_incident_lateness_is_five_hours_nine_minutes():
    assert lateness(MORNING_CRON, INCIDENT_NOW) == timedelta(hours=5, minutes=9)


def test_afternoon_scheduled_fire_same_day():
    now = datetime(2026, 8, 27, 8, 35, tzinfo=timezone.utc)
    assert scheduled_fire_utc(AFTERNOON_CRON, now) == datetime(
        2026, 8, 27, 8, 30, tzinfo=timezone.utc
    )
    assert lateness(AFTERNOON_CRON, now) == timedelta(minutes=5)


def test_on_time_run_has_zero_lateness():
    now = datetime(2026, 8, 27, 8, 30, tzinfo=timezone.utc)
    assert lateness(AFTERNOON_CRON, now) == timedelta(0)


def test_lateness_accepts_non_utc_aware_input():
    """呼叫端若傳台灣時間（tz-aware）也要算對 —— 時區換算不得靠巧合。"""
    tw = timezone(timedelta(hours=8))
    now_tw = INCIDENT_NOW.astimezone(tw)      # 2026-08-27 12:39 +08:00
    assert lateness(MORNING_CRON, now_tw) == timedelta(hours=5, minutes=9)


def test_naive_now_is_rejected():
    """naive datetime 一律拒收：靜默錯位 8 小時正是本次事故的近親。"""
    with pytest.raises(ScheduleError):
        lateness(MORNING_CRON, datetime(2026, 8, 27, 4, 39))


def test_scheduled_fire_rejects_unregistered_cron():
    with pytest.raises(ScheduleError):
        scheduled_fire_utc("0 12 * * *", INCIDENT_NOW)


def test_non_fixed_point_cron_fails_loud(monkeypatch):
    """`*/5` 這類無單一排定時刻的寫法 → Fail-Loud，不得瞎猜一個時分。"""
    monkeypatch.setitem(sch.CRON_SESSIONS, "*/5 * * * *", "morning")
    with pytest.raises(ScheduleError) as exc:
        scheduled_fire_utc("*/5 * * * *", INCIDENT_NOW)
    assert "純數字" in str(exc.value)


# ------------------------------------------------------------------ 遲到閘
def test_morning_gate_boundary_89_vs_91():
    """morning 門檻 90 分：89 分放行、91 分擋下（邊界不得漂移）。"""
    assert is_too_late("morning", timedelta(minutes=89)) is False
    assert is_too_late("morning", timedelta(minutes=90)) is False   # 等於門檻仍放行
    assert is_too_late("morning", timedelta(minutes=91)) is True


def test_afternoon_uses_its_own_threshold():
    """afternoon 用自己的 240 分門檻 —— 別退化成單一門檻。"""
    assert is_too_late("afternoon", timedelta(minutes=91)) is False
    assert is_too_late("afternoon", timedelta(minutes=239)) is False
    assert is_too_late("afternoon", timedelta(minutes=241)) is True
    assert SESSION_MAX_DELAY_MIN["morning"] != SESSION_MAX_DELAY_MIN["afternoon"]


def test_incident_run_is_gated():
    """事故當下：場次判對（morning）之後，遲到閘仍應擋下這次推播。"""
    session = resolve_session(explicit=None, cron=MORNING_CRON)
    assert is_too_late(session, lateness(MORNING_CRON, INCIDENT_NOW)) is True


def test_unknown_session_gate_raises():
    with pytest.raises(ScheduleError):
        is_too_late("midnight", timedelta(0))


# ------------------------------------------------------------------ config SSOT 契約
def test_every_registered_cron_maps_to_known_session():
    assert set(CRON_SESSIONS.values()) <= set(SESSION_LABELS)
    assert set(SESSION_LABELS) <= set(SESSION_MAX_DELAY_MIN)
