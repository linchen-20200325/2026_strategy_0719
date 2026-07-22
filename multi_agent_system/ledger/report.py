"""report.py — ledger 對帳聚合（stateless）。L2 純函式。

讀入判讀清單 + 當前 market_index 交易日 open 序列 → 逐筆 reconcile → 聚合命中率。
**每次重算**（不依賴任何落地的評分狀態）→ 冪等、無漂移。純函式、無 I/O。

命中率一律顯示樣本數 n;n < LEDGER_MIN_MEANINGFUL_SAMPLE → 附「樣本少」旗標，防過早下結論。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from config import (
    LEDGER_HIT_BAND_RATIO,
    LEDGER_HORIZON_TRADING_DAYS,
    LEDGER_MIN_MEANINGFUL_SAMPLE,
    REGIME_LABEL_BEAR,
    REGIME_LABEL_BULL,
    REGIME_LABEL_NEUTRAL,
)

from .reconcile import STATUS_SCORED, PriceBar, reconcile
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
    horizon_n: int
    band: float


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
    band = LEDGER_HIT_BAND_RATIO if band is None else band

    deduped = dedup_judgments(judgments)
    n = {b: 0 for b in _BUCKETS}
    hits = {b: 0 for b in _BUCKETS}
    rets: dict[str, list[float]] = {b: [] for b in _BUCKETS}
    n_scored = n_pending = 0

    for j in deduped:
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

    buckets = {
        b: BucketStat(b, n[b], hits[b],
                      hits[b] / n[b] if n[b] else None, _mean(rets[b]))
        for b in _BUCKETS
    }
    dn = n[REGIME_LABEL_BULL] + n[REGIME_LABEL_BEAR]
    dh = hits[REGIME_LABEL_BULL] + hits[REGIME_LABEL_BEAR]
    return LedgerReport(
        n_total=len(deduped), n_scored=n_scored, n_pending=n_pending,
        directional_hit_rate=(dh / dn if dn else None), directional_n=dn,
        buckets=buckets, horizon_n=horizon_n, band=band,
    )


def format_report(rep: LedgerReport) -> str:
    """對帳報表 → 純文字（console / LINE 共用）。"""
    lines = [f"📒 判讀對帳（forward-test T+{rep.horizon_n} 交易日）"]
    lines.append(f"樣本 {rep.n_total} 筆：已對帳 {rep.n_scored} / 未到期 {rep.n_pending}")

    if rep.directional_hit_rate is None:
        lines.append("方向命中率：尚無已對帳樣本（等 T+N 到期）")
    else:
        warn = "　⚠️ 樣本少，僅供參考" if rep.directional_n < LEDGER_MIN_MEANINGFUL_SAMPLE else ""
        lines.append(
            f"方向命中率 {rep.directional_hit_rate:.0%}"
            f"（{rep.directional_n} 筆偏多+偏空）{warn}"
        )
    for b in _BUCKETS:
        st = rep.buckets[b]
        if st.n == 0:
            continue
        hr = f"{st.hit_rate:.0%}" if st.hit_rate is not None else "—"
        avg = f"{st.avg_forward_return:+.1%}" if st.avg_forward_return is not None else "—"
        lines.append(f"　{b} {st.n} 筆　命中 {st.hits}（{hr}）　平均前瞻 {avg}")
    return "\n".join(lines)
