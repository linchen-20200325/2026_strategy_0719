"""components.py — Streamlit render 元件（drop-in 進既有 dashboard）。

只負責「畫」,資料整形全在 view_model.py。每個函式都可獨立呼叫,
最上層 `render_decision_panel` / `render_cycle_result` 是一鍵嵌入的完整面板。

無縫整合:
    from multi_agent_system.ui import render_cycle_result
    render_cycle_result(orchestrator.run_once(request))   # 就這一行

主題:使用既有 dashboard 的 traffic-light 配色(theme.Palette),半透明底色在明暗主題皆可讀。
"""

from __future__ import annotations

from collections.abc import Sequence

import altair as alt
import streamlit as st

from ..contracts import Action, CycleResult, FinalDecision
from . import view_model as vm
from .theme import DEFAULT_PALETTE, Palette


def render_signal_badge(decision: FinalDecision, *, palette: Palette = DEFAULT_PALETTE) -> None:
    """大張彩色訊號徽章(行動 + Final Score)。"""
    av = vm.action_visual(decision.action, palette)
    bg = vm.hex_to_rgba(av.hex, 0.14)
    if decision.abstained:
        sub = "⚠️ 資料不足,暫停決策 (abstain)"
    elif decision.risk_control_triggered:
        sub = "🚨 集中度風控減碼生效"
    else:
        sub = ""
    st.markdown(
        f"""<div style="border-left:6px solid {av.hex};background:{bg};
             padding:14px 18px;border-radius:8px;margin-bottom:8px;">
          <div style="font-size:0.85rem;opacity:.7;">標的 {decision.tw_stock_id}</div>
          <div style="font-size:1.7rem;font-weight:700;color:{av.hex};">{av.emoji} {av.label}</div>
          <div style="font-size:0.92rem;opacity:.85;">Final Score：
             <b>{vm.final_score_text(decision)}</b>　{sub}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_score_breakdown(
    decision: FinalDecision, *, palette: Palette = DEFAULT_PALETTE
) -> None:
    """三專家得分長條圖(單一藍,身分靠 y 軸標籤,長度表大小)+ 展開診斷原因。"""
    df = vm.breakdown_chart_df(decision)
    base = alt.Chart(df).encode(
        y=alt.Y("expert:N", sort=list(df["expert"]), title=None),
        x=alt.X("score:Q", scale=alt.Scale(domain=[0, 1]), title="專家得分 (0–1，越高越偏多)"),
    )
    bars = base.mark_bar(color=palette.bar, cornerRadiusEnd=4)
    labels = base.mark_text(align="left", dx=4, baseline="middle").encode(
        text="score_label:N"
    )
    st.altair_chart((bars + labels).properties(height=150), use_container_width=True)

    with st.expander("各專家診斷原因"):
        for r in vm.score_breakdown(decision):
            status = "" if r.available else "（資料不足）"
            st.markdown(f"**{r.label}**（{r.weight:.0%}）{status}：{r.reason}")


def render_provenance(packet) -> None:
    """資料血緣一行 + 告警展開(缺料透明化,不靜默)。"""
    if packet is None:
        return
    bits: list[str] = []
    if packet.technical is not None:
        bits.append(f"技術面 {packet.technical.as_of}")
    if packet.us_link is not None:
        bits.append(f"美股 {packet.us_link.us_stock_id} {packet.us_link.as_of}")
    bits.append(f"新聞 {packet.news_count} 則")
    if packet.news_sentiment_mean is not None:
        bits.append(f"情緒 {packet.news_sentiment_mean:+.2f}")
    st.caption("　·　".join(bits))
    if packet.warnings:
        with st.expander(f"⚠️ 資料告警（{len(packet.warnings)}）"):
            for w in packet.warnings:
                st.write("• " + w)


def render_mock_order(receipt) -> None:
    """Mock 下單回執(清楚標示模擬,未真實成交)。"""
    if receipt is None:
        st.caption("🧪 本輪無下單（Hold / abstain 或未開啟 auto_trade）")
        return
    st.caption(
        f"🧪 Mock 下單：{receipt.side} {receipt.symbol} × {receipt.quantity:g}"
        f"　`{receipt.order_id}`　[{receipt.status}]（模擬,未真實成交）"
    )


def render_decision_panel(
    decision: FinalDecision,
    *,
    packet=None,
    receipt=None,
    palette: Palette = DEFAULT_PALETTE,
) -> None:
    """完整決策面板(徽章 + 得分圖 + 血緣 + Mock 下單)。一鍵嵌入。"""
    render_signal_badge(decision, palette=palette)
    render_score_breakdown(decision, palette=palette)
    render_provenance(packet)
    render_mock_order(receipt)


def render_cycle_result(result: CycleResult, *, palette: Palette = DEFAULT_PALETTE) -> None:
    """對 orchestrator.run_once() 的輸出一鍵渲染。"""
    render_decision_panel(
        result.decision, packet=result.packet, receipt=result.receipt, palette=palette
    )


def render_notification_center(
    results: Sequence,
    *,
    palette: Palette = DEFAULT_PALETTE,
    only_actionable: bool = True,
) -> None:
    """通知中心小元件(多標的訊號摘要條,可放 sidebar / dashboard 頂端)。

    results：list[CycleResult] 或 list[FinalDecision] 皆可。
    only_actionable=True 時略過 Hold / abstain,只顯示買賣訊號。
    """
    st.markdown("#### 🔔 訊號通知中心")
    shown = 0
    for item in results:
        decision = item.decision if isinstance(item, CycleResult) else item
        if only_actionable and (decision.abstained or decision.action == Action.HOLD):
            continue
        av = vm.action_visual(decision.action, palette)
        bg = vm.hex_to_rgba(av.hex, 0.10)
        st.markdown(
            f"""<div style="padding:6px 10px;border-left:4px solid {av.hex};
                 margin:4px 0;background:{bg};border-radius:6px;">
              <b>{decision.tw_stock_id}</b>　{av.emoji}
              <span style="color:{av.hex};font-weight:600;">{av.label}</span>
              <span style="opacity:.7;">Final {vm.final_score_text(decision)}</span>
            </div>""",
            unsafe_allow_html=True,
        )
        shown += 1
    if shown == 0:
        st.caption("目前無達到通知門檻的訊號（Hold / abstain 已略過）。")
