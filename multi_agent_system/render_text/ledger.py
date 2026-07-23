"""ledger.py — ledger 對帳報表文字渲染（console / LINE 共用）。

把 L2 聚合結果（LedgerReport / EquityReport）攤成純文字。純顯示層、無計算、無 I/O：
數字與判定全由 `multi_agent_system.ledger.report` 的 build_report / build_equity 算好後傳入，
本層僅 duck-typing 讀屬性排版（不 import 資料來源模組，避免載入期循環相依）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from config import (
    LEDGER_MIN_MEANINGFUL_SAMPLE,
    REGIME_LABEL_BEAR,
    REGIME_LABEL_BULL,
    REGIME_LABEL_NEUTRAL,
)

if TYPE_CHECKING:
    from ..ledger.report import EquityReport, LedgerReport

# 對照 ledger.report._BUCKETS：桶順序（利多/中性/偏空）走 config SSOT 三標籤，byte-identical。
_BUCKETS = (REGIME_LABEL_BULL, REGIME_LABEL_NEUTRAL, REGIME_LABEL_BEAR)


def format_report(rep: LedgerReport) -> str:
    """對帳報表 → 純文字（console / LINE 共用）。"""
    lines = [f"📒 判讀對帳（forward-test T+{rep.horizon_n} 交易日，容差 ±{rep.band:.1%}）"]
    sim = f" / 模擬排除 {rep.n_simulated}" if rep.n_simulated else ""
    lines.append(f"樣本 {rep.n_total} 筆：已對帳 {rep.n_scored} / 未到期 {rep.n_pending}{sim}")

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
        # 對照「無腦永遠喊該桶」的漂移基準 → 超額才是真 edge（漂移 ≠ 本事）。
        base = rep.base_rates.get(b)
        edge_txt = ""
        if base is not None and st.hit_rate is not None:
            edge_txt = f"　基準 {base:.0%} → 超額 {st.hit_rate - base:+.0%}"
        lines.append(f"　{b} {st.n} 筆　命中 {st.hits}（{hr}）{edge_txt}　平均前瞻 {avg}")
    if rep.by_regime:
        lines.append("— 分 regime（方向命中率）—")
        for r, (rn, rh, rr) in rep.by_regime.items():
            lines.append(f"　{r}：{rr:.0%}（{rh}/{rn}）")
    return "\n".join(lines)


def format_equity(eq: EquityReport) -> str:
    """機械式跟單淨值 → 純文字。"""
    if eq.n_segments == 0:
        return "📈 機械式跟單：尚無足夠判讀連段（等下一筆判讀）"
    return (
        f"📈 機械式跟單 vs 大盤（{eq.n_segments} 段 · 換手 {eq.n_switches} 次 · 已扣成本）\n"
        f"　跟單 {eq.strategy_return:+.1%}　vs　大盤 {eq.market_return:+.1%}"
        f"　→ 超額 {eq.excess:+.1%}"
    )
