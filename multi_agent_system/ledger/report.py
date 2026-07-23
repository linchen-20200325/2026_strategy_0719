"""report.py — ledger 對帳聚合（stateless、純 L2）。

讀入判讀清單 + 當前 market_index 交易日 open 序列 → 逐筆 reconcile → 聚合命中率。
**每次重算**（不依賴任何落地的評分狀態）→ 冪等、無漂移。純函式、無 I/O、無文字渲染。

命中率一律顯示樣本數 n;n < LEDGER_MIN_MEANINGFUL_SAMPLE → 由 render_text.ledger 附「樣本少」
旗標，防過早下結論。文字渲染（format_report / format_equity）已於 Phase 3 V2 遷至
`multi_agent_system.render_text.ledger`;呼叫端請直接自 `render_text` 匯入（本檔不再 re-export）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from config import (
    LEDGER_EXPOSURE,
    LEDGER_HIT_BAND_RATIO,
    LEDGER_HORIZON_TRADING_DAYS,
    LEDGER_SWITCH_COST_RATIO,
    REGIME_LABEL_BEAR,
    REGIME_LABEL_BULL,
    REGIME_LABEL_NEUTRAL,
)

from .reconcile import STATUS_SCORED, PriceBar, _entry_index, horizon_band, reconcile
from .store import Judgment

_BUCKETS = (REGIME_LABEL_BULL, REGIME_LABEL_NEUTRAL, REGIME_LABEL_BEAR)


@dataclass(frozen=True)
class BucketStat:
    label: str
    n: int                              # 已對帳筆數
    hits: int
    hit_rate: float | None              # hits / n（n=0 → None）
    avg_forward_return: float | None    # 平均前瞻報酬（比例；n=0 → None）


@dataclass(frozen=True)
class LedgerReport:
    n_total: int                        # 去重後判讀總數
    n_scored: int                       # 已到 T+N、已對帳
    n_pending: int                      # 未到 T+N / 無進場
    directional_hit_rate: float | None  # 偏多+偏空 命中率（中性不計方向）
    directional_n: int
    buckets: dict                       # label -> BucketStat
    by_regime: dict                     # regime -> (n, hits, hit_rate)（僅方向判讀）
    horizon_n: int
    band: float                         # 視窗**有效**容差（日容差 × √horizon）
    base_rates: dict = field(default_factory=dict)  # label -> 基準命中率（always-該桶；漂移 base rate）
    n_simulated: int = 0                # 模擬總經判讀（排除不計成績；F Fail-Loud）


def dedup_judgments(judgments: list[Judgment]) -> list[Judgment]:
    """同 (judged_date, session) 取最後一筆（同日重跑不重複計）;保出現序。"""
    seen: dict[tuple[str, str], Judgment] = {}
    for j in judgments:
        seen[(j.judged_date, j.session)] = j
    return list(seen.values())


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def build_report(
    judgments: list[Judgment],
    bars: list[PriceBar],
    *,
    horizon_n: int | None = None,
    band: float | None = None,
) -> LedgerReport:
    horizon_n = LEDGER_HORIZON_TRADING_DAYS if horizon_n is None else horizon_n
    # band bug 修正：config 值為「每日 no-move 容差」→ 對帳時 ×√horizon 放大到視窗尺度
    # （否則日容差硬套月報酬 → 中性桶結構性 0% 命中）。呼叫端顯式傳 band 則視為「已是
    # 視窗有效容差」原樣用（測試/特殊視窗，不重複放大）。
    band = horizon_band(LEDGER_HIT_BAND_RATIO, horizon_n) if band is None else band

    deduped = dedup_judgments(judgments)
    n = {b: 0 for b in _BUCKETS}
    hits = {b: 0 for b in _BUCKETS}
    rets: dict[str, list[float]] = {b: [] for b in _BUCKETS}
    base_cnt = {b: 0 for b in _BUCKETS}   # 市場實際走勢分布（always-該桶 基準；漂移 base rate）
    n_scored = n_pending = n_simulated = 0
    reg_n: dict[str, int] = {}      # 分 regime：僅方向判讀（偏多/偏空）
    reg_hits: dict[str, int] = {}

    for j in deduped:
        # F：模擬/注入總經的判讀 → 排除不計成績（§1 錯值比缺值危險，不讓假總經污染 track record）。
        if getattr(j, "is_simulated", False):
            n_simulated += 1
            continue
        out = reconcile(
            label=j.label, judged_date=date.fromisoformat(j.judged_date),
            session=j.session, bars=bars, horizon_n=horizon_n, band=band,
        )
        if out.status != STATUS_SCORED:
            n_pending += 1
            continue
        n_scored += 1
        n[j.label] += 1
        if out.hit:
            hits[j.label] += 1
        rets[j.label].append(out.forward_return)
        # 基準：這些對帳日市場**實際**走勢分布（漲>容差 / 幾乎沒動 / 跌>容差），
        # 與判讀 label 無關 → 「無腦永遠喊某桶」的命中率（漂移 base rate）。
        r = out.forward_return
        if r > band:
            base_cnt[REGIME_LABEL_BULL] += 1
        elif r < -band:
            base_cnt[REGIME_LABEL_BEAR] += 1
        else:
            base_cnt[REGIME_LABEL_NEUTRAL] += 1
        if j.label in (REGIME_LABEL_BULL, REGIME_LABEL_BEAR):
            reg_n[j.regime] = reg_n.get(j.regime, 0) + 1
            if out.hit:
                reg_hits[j.regime] = reg_hits.get(j.regime, 0) + 1

    buckets = {
        b: BucketStat(b, n[b], hits[b],
                      hits[b] / n[b] if n[b] else None, _mean(rets[b]))
        for b in _BUCKETS
    }
    dn = n[REGIME_LABEL_BULL] + n[REGIME_LABEL_BEAR]
    dh = hits[REGIME_LABEL_BULL] + hits[REGIME_LABEL_BEAR]
    by_regime = {
        r: (reg_n[r], reg_hits.get(r, 0), reg_hits.get(r, 0) / reg_n[r])
        for r in reg_n
    }
    base_rates = {
        b: (base_cnt[b] / n_scored if n_scored else None) for b in _BUCKETS
    }
    return LedgerReport(
        n_total=len(deduped), n_scored=n_scored, n_pending=n_pending,
        directional_hit_rate=(dh / dn if dn else None), directional_n=dn,
        buckets=buckets, by_regime=by_regime, horizon_n=horizon_n, band=band,
        base_rates=base_rates, n_simulated=n_simulated,
    )


# ------------------------------------------------------------------ 機械式跟單淨值
@dataclass(frozen=True)
class EquityReport:
    """跟著判讀機械式做的假設淨值 vs 大盤買入持有（stateless，含換手成本）。"""

    n_segments: int
    strategy_return: float | None   # 跟單累積報酬（比例）
    market_return: float | None     # 大盤買入持有（比例）
    excess: float | None            # 跟單 − 大盤（超額）
    n_switches: int
    switch_cost: float
    n_simulated: int = 0            # 模擬總經判讀（排除不計淨值；§1，比照 LedgerReport）


_SESSION_RANK = {"morning": 0, "afternoon": 1}


def build_equity(
    judgments: list[Judgment],
    bars: list[PriceBar],
    *,
    exposure: dict | None = None,
    switch_cost: float | None = None,
) -> EquityReport:
    """機械式跟單淨值 vs 大盤：判讀依序連段，每段 exposure × 大盤 open-to-open 報酬，累乘。

    段報酬 = open[下一判讀進場] / open[本判讀進場] − 1；換手成本 = switch_cost × |曝險變動|。
    最後一筆無「下一段」→ 不計（pending）；進場超出資料範圍或零長度段 → skip。
    §1：模擬/注入總經判讀**排除不計淨值**（與 build_report 一致，不讓假總經污染跟單成績），
    排除數以 n_simulated 揭露；market_index open 非正 → Fail-Loud raise（比照 reconcile）。
    """
    exposure = LEDGER_EXPOSURE if exposure is None else exposure
    switch_cost = LEDGER_SWITCH_COST_RATIO if switch_cost is None else switch_cost
    deduped = dedup_judgments(judgments)
    n_simulated = sum(1 for j in deduped if getattr(j, "is_simulated", False))
    real = [j for j in deduped if not getattr(j, "is_simulated", False)]
    seq = sorted(real, key=lambda j: (j.judged_date, _SESSION_RANK.get(j.session, 9)))
    entries = [_entry_index(bars, date.fromisoformat(j.judged_date), j.session) for j in seq]

    strat = mkt = 1.0
    nseg = nsw = 0
    prev_exp = 0.0
    for i in range(len(seq) - 1):
        ei, ej = entries[i], entries[i + 1]
        if ei is None or ej is None or ej <= ei:      # 無法成段（缺進場 / 零長度）
            continue
        entry_open = bars[ei].open
        if entry_open <= 0:      # §1：open 非正 = market_index 資料異常，Fail-Loud（不算假報酬）
            raise ValueError(
                f"market_index open 非正（{entry_open} @ {bars[ei].d}），無法算跟單段報酬"
            )
        seg_ret = bars[ej].open / entry_open - 1.0
        exp = exposure.get(seq[i].label, 0.0)
        strat *= 1.0 + exp * seg_ret - switch_cost * abs(exp - prev_exp)
        mkt *= 1.0 + seg_ret
        if exp != prev_exp:
            nsw += 1
        prev_exp = exp
        nseg += 1

    if nseg == 0:
        return EquityReport(0, None, None, None, 0, switch_cost, n_simulated)
    sr, mr = strat - 1.0, mkt - 1.0
    return EquityReport(nseg, sr, mr, sr - mr, nsw, switch_cost, n_simulated)
