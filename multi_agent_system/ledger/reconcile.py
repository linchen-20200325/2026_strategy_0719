"""reconcile.py — 判讀對帳純函式（forward-test 評分核心）。

給定一筆「大盤判讀」(label + 判讀日 + session) 與一段交易日 open 價序列 (market_index)，
算出 entry/exit open、前瞻報酬、命中與否。**純函式、無 I/O、無 DB** —— 價序列由呼叫端
(store/CLI) 傳入，此層只做「數學 + 時序對齊」，因此最好測。

PIT 保證（防 lookahead，§2.3）:
* entry = 判讀後**第一個可成交 open**:盤前判讀(morning)= 判讀日當天 open(09:00 可買到);
  收盤後判讀(afternoon)= **次一交易日** open(當日已收盤，最快隔日才進得去)。
* exit  = entry 之後 **N 個交易日**的 open。**open-to-open**，不用「已看過的收盤價」→ 無 lookahead。
* market_index 每一列 = 一個交易日 → 「N 交易日後」= 往後數 N 列(週末假日自動略過，
  不需第三方 trading-calendar lib，對照 CLAUDE.md §4.5)。

單位: forward_return 為**比例**(0.03 = 3%)，band 亦為比例(0.005 = 0.5%)，不混百分點(§4.1)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import sqrt

from config import REGIME_LABEL_BEAR, REGIME_LABEL_BULL, REGIME_LABEL_NEUTRAL

# 對帳狀態（Fail-Loud：資料不足不評分，明確標 pending，不臆造）。
STATUS_SCORED = "scored"     # 已對帳(有 hit)
STATUS_PENDING = "pending"   # 尚未到 T+N 或缺進場資料 → 下次再試


@dataclass(frozen=True)
class PriceBar:
    """單一交易日的 open（自 market_index 抽出；升冪排列由呼叫端保證）。"""

    d: date
    open: float


@dataclass(frozen=True)
class ReconcileOutcome:
    """一筆判讀的對帳結果（Fail-Loud：pending 時數值欄為 None + reason 說明）。"""

    status: str                    # STATUS_SCORED / STATUS_PENDING
    bucket: str                    # 判讀 label（偏多/中性/偏空）
    entry_date: date | None
    entry_open: float | None
    exit_date: date | None
    exit_open: float | None
    forward_return: float | None   # 比例：exit/entry - 1
    hit: bool | None
    reason: str                    # pending 原因 / "ok"


def forward_return(entry_open: float, exit_open: float) -> float:
    """前瞻報酬（比例）= exit/entry - 1。entry <= 0 → raise（不 silent ÷0，§4.4）。"""
    if entry_open <= 0:
        raise ValueError(f"entry_open 必須為正，收到 {entry_open!r}")
    return exit_open / entry_open - 1.0


def classify_hit(label: str, fwd_ret: float, band: float) -> bool:
    """命中判定（band 為比例）。

    偏多 → 漲超過 band 才算命中;偏空 → 跌超過 band;中性 → 幾乎沒動(|報酬| <= band)。
    未知 label / 負 band → raise（Fail-Loud，寧可炸掉不誤判）。
    """
    if band < 0:
        raise ValueError(f"band 不可為負：{band!r}")
    if label == REGIME_LABEL_BULL:
        return fwd_ret > band
    if label == REGIME_LABEL_BEAR:
        return fwd_ret < -band
    if label == REGIME_LABEL_NEUTRAL:
        return abs(fwd_ret) <= band
    raise ValueError(
        f"未知判讀 label：{label!r}（應為 "
        f"{REGIME_LABEL_BULL}/{REGIME_LABEL_NEUTRAL}/{REGIME_LABEL_BEAR}）"
    )


def horizon_band(daily_band: float, horizon_n: int) -> float:
    """把「每日 no-move 容差」放大到 N 交易日視窗尺度（隨機漫步 vol ∝ √時間）。

    修正 band bug：0.5% 這種**日尺度**容差直接套在 **20 交易日**前瞻報酬上 → 中性命中
    條件「|月報酬| ≤ 0.5%」幾乎不可能成立 → 中性桶結構性接近 0% 命中。放大後
    有效容差 = daily_band × √horizon_n（如 0.5% × √20 ≈ 2.24%），才是「一個月幾乎
    沒動」的合理尺度。非新增臆造常數，而是用隨機漫步原理從日換算到視窗。

    負 daily_band / 非正 horizon → raise（Fail-Loud，§1）。
    """
    if daily_band < 0:
        raise ValueError(f"daily_band 不可為負：{daily_band!r}")
    if horizon_n <= 0:
        raise ValueError(f"horizon_n 必須為正：{horizon_n!r}")
    return daily_band * sqrt(horizon_n)


def _entry_index(bars: list[PriceBar], judged_date: date, session: str) -> int | None:
    """進場列索引。morning=判讀日當天(或其後第一交易日)open;其餘(afternoon)=嚴格次一交易日。

    找不到（判讀日之後尚無任何交易日 open）→ None。
    """
    for i, b in enumerate(bars):
        if session == "morning":
            if b.d >= judged_date:      # 盤前 → 判讀日當天 open 即可成交
                return i
        elif b.d > judged_date:         # 收盤後 → 最快次一交易日 open
            return i
    return None


def reconcile(
    *,
    label: str,
    judged_date: date,
    session: str,
    bars: list[PriceBar],
    horizon_n: int,
    band: float,
) -> ReconcileOutcome:
    """對帳一筆大盤判讀。

    bars: 升冪 (date, open) 交易日序列（market_index 抽出）。
    horizon_n: 前瞻交易日數。band: 命中容差（比例）。
    """
    if horizon_n <= 0:
        raise ValueError(f"horizon_n 必須為正：{horizon_n!r}")
    # 升冪防呆（linear scan 假設升冪；違反則進出場對齊會錯 → Fail-Loud）。
    for i in range(len(bars) - 1):
        if bars[i].d > bars[i + 1].d:
            raise ValueError("bars 必須按日期升冪排列")

    ei = _entry_index(bars, judged_date, session)
    if ei is None:
        return ReconcileOutcome(
            STATUS_PENDING, label, None, None, None, None, None, None,
            "判讀日之後尚無交易日 open（進場價未定）",
        )
    xi = ei + horizon_n
    if xi >= len(bars):
        entry = bars[ei]
        return ReconcileOutcome(
            STATUS_PENDING, label, entry.d, entry.open, None, None, None, None,
            f"未到 T+{horizon_n}（缺出場交易日 open）",
        )
    entry, exit_ = bars[ei], bars[xi]
    ret = forward_return(entry.open, exit_.open)
    hit = classify_hit(label, ret, band)
    return ReconcileOutcome(
        STATUS_SCORED, label, entry.d, entry.open, exit_.d, exit_.open, ret, hit, "ok",
    )
