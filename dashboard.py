"""dashboard.py — 獨立 Streamlit 展示頁（`streamlit run dashboard.py`）。

展示決策面板 + 通知中心小元件如何呈現;同時作為「無縫嵌入既有 dashboard」的參考範例
（把 render_cycle_result 那一行搬進你 dashboard 的任一 tab 即可）。
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from multi_agent_system import (
    DataAggregationAgent,
    MockBrokerAPI,
    ResearchRequest,
    SimulatedMacroProvider,
    WorkflowOrchestrator,
    default_portfolio_state,
)
from multi_agent_system.ui import render_cycle_result, render_notification_center
from scripts.seed_demo_dbs import default_demo_dir, seed_all

AS_OF = date(2026, 7, 19)

# 情境預設(對應 demo 資料)：股號 → (連動美股, 關鍵字, 權重, sharpe, 總經情境, 利差, CPI)
_PRESETS = {
    "2330（超賣範例）": ("NVDA", ["台積電", "半導體", "TSMC"], 0.10, 1.4, "healthy", 1.2, 2.4),
    "2454（超買範例）": ("AMD", ["半導體"], 0.35, 0.8, "stagflation", -0.4, 5.6),
    "9999（查無範例）": ("NVDA", ["台積電"], 0.05, 1.0, "healthy", 1.0, 2.0),
}


@st.cache_resource
def _get_orchestrator() -> tuple[WorkflowOrchestrator, MockBrokerAPI]:
    paths = seed_all(default_demo_dir())
    agent = DataAggregationAgent(paths["stock_db"], paths["fund_db"], paths["news_db"])
    broker = MockBrokerAPI()
    return WorkflowOrchestrator(agent, broker=broker), broker


def _make_request(preset_key: str, weight: float, auto_trade: bool) -> ResearchRequest:
    us, kw, _w, sharpe, scenario, spread, cpi = _PRESETS[preset_key]
    tw_id = preset_key.split("（")[0]
    return ResearchRequest(
        tw_stock_id=tw_id,
        us_stock_id=us,
        news_keywords=kw,
        portfolio_state=default_portfolio_state(weight, sharpe=sharpe),
        macro_provider=SimulatedMacroProvider(
            yield_spread_pct=spread, cpi_yoy_pct=cpi, scenario=scenario
        ),
        as_of_date=AS_OF,
        auto_trade=auto_trade,
    )


def main() -> None:
    st.set_page_config(page_title="多智能體投研訊號", page_icon="🧠", layout="wide")
    st.title("🧠 多智能體投研訊號面板")
    st.caption("三庫供料 → 6 個 agent 分析 → 決策融合 → 訊號 + Mock 下單（模擬情境）")

    orch, _broker = _get_orchestrator()

    with st.sidebar:
        st.header("設定")
        preset = st.selectbox("標的情境", list(_PRESETS.keys()))
        default_w = _PRESETS[preset][2]
        weight = st.slider("目前持股權重", 0.0, 1.0, float(default_w), 0.01)
        auto_trade = st.toggle("啟用 Mock 下單", value=True)
        st.divider()
        # 通知中心:一次跑三個 demo 標的,只顯示可行動訊號
        all_results = [
            orch.run_once(_make_request(k, _PRESETS[k][2], auto_trade=False))
            for k in _PRESETS
        ]
        render_notification_center(all_results)

    # 主面板:當前選擇標的
    result = orch.run_once(_make_request(preset, weight, auto_trade))
    render_cycle_result(result)


if __name__ == "__main__":
    main()
