"""test_stock_ledger_record.py — A Phase 1：個股判讀落帳（store + recorder）。

覆蓋:store round-trip / 空 / 損毀列 raise / 路徑解析（env vs 顯式）；recorder 從
CycleResult 落帳（action.name、abstain→None、缺技術面誠實 None 不捏價、失敗不 raise
不擋推播 §8.1#5）。對帳（Phase 2）不在本階段，故不涉及。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from multi_agent_system.contracts import Action
from multi_agent_system.ledger.stock_recorder import record_stock_judgments
from multi_agent_system.ledger.stock_store import (
    StockJudgment,
    append_stock_judgments,
    read_stock_judgments,
)

TW = timezone(timedelta(hours=8))
_WHEN = datetime(2026, 7, 22, 7, 30, tzinfo=TW)


def _result(sid, action, score, abst, close, as_of):
    """duck-typed CycleResult 替身（recorder 只碰 .decision + .packet.technical）。"""
    tech = None if close is None else NS(close=close, as_of=as_of)
    return NS(
        decision=NS(tw_stock_id=sid, action=action, final_score=score, abstained=abst),
        packet=NS(technical=tech),
    )


# ───────────────────────────────────────── store round-trip / 邊界
def test_store_roundtrip(tmp_path):
    p = str(tmp_path / "s.jsonl")
    js = [
        StockJudgment("2026-07-22T07:30:00+08:00", "2026-07-22", "morning",
                      "2330", "STRONG_BUY", 0.82, False, 1050.0, "2026-07-21"),
        StockJudgment("2026-07-22T07:30:00+08:00", "2026-07-22", "morning",
                      "9999", "HOLD", None, True, None, None),
    ]
    assert append_stock_judgments(js, path=p) == 2
    rows = read_stock_judgments(path=p)
    assert [r.stock_id for r in rows] == ["2330", "9999"]        # 升冪即寫入序
    assert rows[0].ref_close == 1050.0
    assert rows[1].final_score is None and rows[1].ref_close is None


def test_store_empty_and_missing(tmp_path):
    p = str(tmp_path / "none.jsonl")
    assert read_stock_judgments(path=p) == []          # 檔不存在 → 空列
    assert append_stock_judgments([], path=p) == 0     # 空批 → 不寫、回 0


def test_store_corrupt_line_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text("{not valid json}\n", encoding="utf-8")
    with pytest.raises(ValueError):                    # 損毀列 → raise（Fail-Loud，§1）
        read_stock_judgments(path=str(p))


def test_store_env_path(tmp_path, monkeypatch):
    p = tmp_path / "env.jsonl"
    monkeypatch.setenv("STOCK_LEDGER_FILE", str(p))
    append_stock_judgments([StockJudgment("x", "2026-07-22", "morning",
                            "2330", "ADD", 0.7, False, 1.0, "2026-07-21")])
    assert len(read_stock_judgments()) == 1            # 無顯式 path → 走 STOCK_LEDGER_FILE


# ───────────────────────────────────────── recorder（從 CycleResult 落帳）
def test_recorder_writes_all_watchlist(tmp_path):
    p = str(tmp_path / "s.jsonl")
    results = [
        _result("2330", Action.STRONG_BUY, 0.82, False, 1050.0, "2026-07-21"),
        _result("2317", Action.HOLD, 0.51, False, 210.5, "2026-07-21"),
        _result("6770", Action.STRONG_SELL, 0.12, False, 88.3, "2026-07-21"),
    ]
    assert record_stock_judgments(results, session="morning", when=_WHEN, path=p) == 3
    rows = read_stock_judgments(path=p)
    assert {r.action for r in rows} == {"STRONG_BUY", "HOLD", "STRONG_SELL"}   # 存 .name
    assert all(r.judged_date == "2026-07-22" and r.session == "morning" for r in rows)
    assert rows[0].ref_close == 1050.0 and rows[0].ref_as_of == "2026-07-21"


def test_recorder_abstain_and_missing_technical_honest_none(tmp_path):
    p = str(tmp_path / "s.jsonl")
    results = [_result("9999", Action.HOLD, None, True, None, None)]   # abstain + 無技術面
    assert record_stock_judgments(results, session="afternoon", when=_WHEN, path=p) == 1
    r = read_stock_judgments(path=p)[0]
    assert r.final_score is None and r.ref_close is None and r.ref_as_of is None  # 不捏價
    assert r.abstained is True and r.action == "HOLD"


def test_recorder_never_raises_never_blocks_push(tmp_path):
    # 壞 result（decision 缺 action 屬性）→ recorder 內部炸,但回 0、絕不往上拋（§8.1#5）
    bad = [NS(decision=NS(tw_stock_id="x", final_score=0.5, abstained=False),
              packet=NS(technical=None))]
    assert record_stock_judgments(bad, session="morning", path=str(tmp_path / "s.jsonl")) == 0


def test_recorder_empty_results(tmp_path):
    assert record_stock_judgments([], session="morning", path=str(tmp_path / "s.jsonl")) == 0
