"""test_tz.py — 台灣時區 SSOT 迴歸（config.TW_TZ / now_tw / today_tw）。

守住盤前班的時區錯位 bug：cron `30 23 * * 0-4`（23:30 UTC 觸發）時，naive
`date.today()` 在 UTC runner 上仍是「前一天」，但所有外部資料（news.db / stock.db /
夜盤 / fund.db）皆以**台灣日期**戳記 → 當日新聞被 [as_of-7, as_of] 日期窗濾掉（→ 無資料）。
故跨層 as_of 一律用 today_tw()。此檔把該行為釘死，避免回頭改成 naive date.today()。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from config import TW_TZ, now_tw, today_tw


def test_tw_tz_is_utc_plus_8():
    assert TW_TZ.utcoffset(None) == timedelta(hours=8)


def test_now_tw_is_tz_aware_plus_8():
    n = now_tw()
    assert n.tzinfo is not None
    assert n.utcoffset() == timedelta(hours=8)


def test_morning_cron_utc_evening_maps_to_tw_next_day():
    # 盤前 cron 23:30 UTC → 台灣已是隔日 07:30；as_of 必須解析為隔日，
    # 否則當日（台灣日期戳記）新聞被日期窗濾掉 —— 正是本次修掉的 bug。
    utc_evening = datetime(2026, 7, 21, 23, 30, tzinfo=timezone.utc)
    assert utc_evening.astimezone(TW_TZ).date().isoformat() == "2026-07-22"


def test_today_tw_returns_date_aligned_to_tw():
    t = today_tw()
    assert isinstance(t, date)
    # 與「以 UTC+8 換算的現在」同日（midnight 邊界極短競態可忽略）。
    assert t == datetime.now(timezone.utc).astimezone(TW_TZ).date()
