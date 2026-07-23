"""test_watch_digest.py — 個股盯盤卡 formatter（format_stock_card / format_watch_digest）。

驗證對齊使用者現有 LINE 盯盤 bot 的卡片：【代號】判讀 → 📊 技術 → 💰 籌碼，
且缺料一律誠實（MA/KD 缺 → 「—」、籌碼全缺 → 略過該行、abstain → 「資料不足」）。
單位：均線=元、KD=0~100、籌碼=張（賣超保留負號 + 千分位）。
"""

from __future__ import annotations

from multi_agent_system.contracts import (
    Action,
    DataPacket,
    FinalDecision,
    TechnicalSnapshot,
)
from multi_agent_system.integration_agent import CycleResult
from multi_agent_system.render_text import format_stock_card, format_watch_digest


def _snap(**kw) -> TechnicalSnapshot:
    base = dict(
        stock_id="6770", as_of="2026-07-18", close=68.9,
        rsi=45.0, upper_band=75.0, lower_band=60.0,
    )
    base.update(kw)
    return TechnicalSnapshot(**base)


def _cycle(action, *, tech, final_score=0.5, abstained=False) -> CycleResult:
    d = FinalDecision(
        tw_stock_id=(tech.stock_id if tech else "0000"),
        action=action,
        final_score=None if abstained else final_score,
        abstained=abstained,
        risk_control_triggered=False,
        verdicts={},
        rationale="test",
    )
    pkt = DataPacket(
        tw_stock_id=d.tw_stock_id, technical=tech, us_link=None,
        news=(), news_sentiment_mean=None, news_count=0,
    )
    return CycleResult(decision=d, packet=pkt, receipt=None)


def test_card_bullish_with_chip_and_ma():
    tech = _snap(
        ma20=71.0, ma60=68.0, kd_k=50.0, kd_d=43.0,
        foreign_net_lots=-115284.0, trust_net_lots=739.0, total_net_lots=-121700.0,
    )
    card = format_stock_card(_cycle(Action.STRONG_BUY, tech=tech, final_score=0.85))
    assert "【6770】🟢 利多" in card
    assert "收68.9" in card
    assert "20MA❌跌破" in card              # 68.9 < 71 → 跌破
    assert "60MA✅站上" in card              # 68.9 > 68 → 站上
    assert "KD 50/43" in card
    assert "RSI 45" in card
    assert "外資-115,284張" in card           # 賣超負號 + 千分位
    assert "投信+739張" in card
    assert "三大法人-121,700張" in card


def test_card_hold_missing_optional_fields():
    # 加料欄全 None → MA/KD 顯示「—」,籌碼行整行略過（不捏造 0）
    card = format_stock_card(_cycle(Action.HOLD, tech=_snap(), final_score=0.5))
    assert "🟡 中性" in card
    assert "20MA —" in card and "60MA —" in card and "KD —" in card
    assert "💰 籌碼" not in card


def test_card_bearish_label():
    card = format_stock_card(_cycle(Action.STRONG_SELL, tech=_snap(), final_score=0.2))
    assert "🔴 利空" in card


def test_card_no_technical_and_abstain():
    card = format_stock_card(_cycle(Action.HOLD, tech=None, abstained=True))
    assert "資料不足" in card
    assert "技術 —" in card


def test_watch_digest_layout_and_footer():
    tech = _snap(
        ma20=71.0, ma60=68.0, kd_k=50.0, kd_d=43.0,
        foreign_net_lots=1000.0, trust_net_lots=0.0, total_net_lots=1200.0,
    )
    dig = format_watch_digest([_cycle(Action.ADD, tech=tech)], day="2026-07-20")
    assert dig.startswith("📈 個股盯盤 2026-07-20")
    assert "指令：加/刪/清單" in dig
    assert "投信+0張" in dig                  # 0 也帶正號（誠實顯示，不省略）


def test_watch_digest_empty():
    assert "清單為空" in format_watch_digest([], day="2026-07-20")
