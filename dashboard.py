"""dashboard.py — 多智能體投研 Streamlit 儀表板。

三個分頁：
  📋 追蹤清單  — 使用者可編輯要追蹤的台股 / ETF / 基金（st.data_editor）。
  📈 利多標的  — 對清單跑 6-agent 分析，篩出利多（強買/加碼）並排序 + LINE 推播。
  🔍 個股決策  — 單一標的完整決策面板（徽章 + 得分圖 + Mock 下單）。

⚠️ 線上 demo 自帶示範資料，只有 2330 / 2454 有數；追蹤自己的標的需接真實三庫（stock/fund/news.db）。
LINE 推播沿用 mynews 慣例（LINE_TO 自動 broadcast / multicast / push），token 走 App secrets。
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from config import DEFAULT_WEIGHT_RATIO
from multi_agent_system import (
    DataAggregationAgent,
    LinePusher,
    MockBrokerAPI,
    ResearchRequest,
    SimulatedMacroProvider,
    WorkflowOrchestrator,
)
from multi_agent_system.line_push import LinePushError
from multi_agent_system.pipeline import (
    DEMO_WATCHLIST,
    WatchItem,
    build_request,
    bullish_ranked,
    watchlist_from_df,
    watchlist_to_df,
)
from multi_agent_system.render_text import format_bullish_digest
from multi_agent_system.subscribers import (
    SubscriberStoreError,
    make_subscriber_store,
    store_is_github,
)
from multi_agent_system.ui import render_cycle_result
from scripts.seed_demo_dbs import default_demo_dir, seed_all

AS_OF = date(2026, 7, 19)   # 對齊 demo 新聞視窗


@st.cache_resource
def _get_orchestrator() -> WorkflowOrchestrator:
    paths = seed_all(default_demo_dir())
    agent = DataAggregationAgent(paths["stock_db"], paths["fund_db"], paths["news_db"])
    return WorkflowOrchestrator(agent, broker=MockBrokerAPI())


def _macro() -> SimulatedMacroProvider:
    # 模擬中性總經（is_simulated=True）;接 FRED 後改注入真實值。
    return SimulatedMacroProvider(yield_spread_pct=1.0, cpi_yoy_pct=2.4, scenario="neutral")


def _request(item: WatchItem, *, auto_trade: bool = False) -> ResearchRequest:
    # ResearchRequest 建構走 pipeline.build_request SSOT（與 cron 批次同源）;
    # dashboard 只固定注入 demo 的模擬總經 _macro() 與 AS_OF，不重刻欄位對應。
    return build_request(item, _macro(), as_of=AS_OF, auto_trade=auto_trade)


def _secret(key: str) -> str | None:
    """安全讀 st.secrets（無 secrets 檔時不炸）;下游再退回環境變數。"""
    try:
        return st.secrets.get(key)  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001 - 無 secrets 檔 → 視為未設定
        return None


def _persist_store():
    """已設 App Secrets 的 GITHUB_TOKEN + GITHUB_REPO → 回 GitHub 持久化 store;否則 None（demo）。"""
    if not store_is_github(get_env=_secret):
        return None
    try:
        return make_subscriber_store(get_env=_secret)
    except SubscriberStoreError as exc:
        st.warning(f"GitHub 清單設定有誤，暫用示範清單：{exc}")
        return None


def _owner_id() -> str | None:
    """dashboard 這份清單掛在哪個 userId 底下（= 你自己的 LINE userId）。"""
    return _secret("WATCH_OWNER_ID")


def _watchlist() -> list[WatchItem]:
    if "watchlist" not in st.session_state:
        store, owner = _persist_store(), _owner_id()
        if store is not None and owner:
            try:
                items = store.get(owner)
                st.session_state.watchlist = items or list(DEMO_WATCHLIST)
            except SubscriberStoreError as exc:
                st.warning(f"讀 GitHub 清單失敗，暫用示範：{exc}")
                st.session_state.watchlist = list(DEMO_WATCHLIST)
        else:
            st.session_state.watchlist = list(DEMO_WATCHLIST)
    return st.session_state.watchlist


# ------------------------------------------------------------------ 分頁

def build_watch_item(category: str, code: str, kw: str) -> WatchItem | None:
    """(類別, 代號, 名稱/關鍵字) → WatchItem;空代號 → None（呼叫端提示）。純函式,好測。

    關鍵字支援全形「，」與半形「,」分隔;權重走 config SSOT,與 nas_line_bot 加標的一致。
    """
    code = (code or "").strip()
    if not code:
        return None
    keywords = tuple(k.strip() for k in (kw or "").replace("，", ",").split(",") if k.strip())
    return WatchItem(
        tw_stock_id=code, us_stock_id="", keywords=keywords,
        current_weight_ratio=DEFAULT_WEIGHT_RATIO, sharpe=None, category=category,
    )


def _add_watch_item(category: str, code: str, kw: str, store, owner: str | None) -> None:
    """手機友善快速新增一檔 → 存清單（設了 GitHub 就寫回）。空代號 → 提示。存完 flash + rerun。"""
    item = build_watch_item(category, code, kw)
    if item is None:
        st.session_state["_watch_flash"] = (False, "請先填代號再按新增。")
        st.rerun()
    wl = _watchlist()
    wl.append(item)
    st.session_state.watchlist = wl
    if store is not None and owner:
        try:
            store.set(owner, wl)
            st.session_state["_watch_flash"] = (
                True, f"已加入 {category} {code}，並存到 GitHub（共 {len(wl)} 檔）。"
            )
        except SubscriberStoreError as exc:
            st.session_state["_watch_flash"] = (
                False, f"已加入 {category} {code}（本次 session），但存 GitHub 失敗：{exc}"
            )
    else:
        st.session_state["_watch_flash"] = (
            True, f"已加入 {category} {code}（共 {len(wl)} 檔，僅本次 session；設 Secrets 才存得住）。"
        )
    st.rerun()


def _tab_watchlist() -> None:
    st.subheader("📋 我的追蹤清單")
    _flash = st.session_state.pop("_watch_flash", None)   # 上一次新增結果（rerun 後才顯示得到）
    if _flash:
        (st.success if _flash[0] else st.error)(_flash[1])
    store, owner = _persist_store(), _owner_id()
    if store is not None and owner:
        st.caption(f"✅ 已接 GitHub 持久化（owner `{owner[:8]}…`）—— 加了會寫進 repo，雲端 / NAS 共用同一份。")
    elif store is not None and not owner:
        st.caption("⚠️ 已設 GITHUB_TOKEN/REPO 但缺 `WATCH_OWNER_ID`（你的 LINE userId）→ 仍只存本次 session。")
    else:
        st.caption(
            "ℹ️ 未接持久化：加了只存本次 session、重整就回示範。要存得住 → App → Settings → Secrets "
            "填 `GITHUB_TOKEN` / `GITHUB_REPO`（owner/name）/ `WATCH_OWNER_ID`（你的 LINE userId）。"
        )
    st.caption(
        "台股 / ETF 代號 → stock.db;連動美股 / 基金 → fund.db;新聞關鍵字 → news.db。"
        "可直接編輯、加列（右下 +）、刪列。"
    )

    # 手機友善快速新增：手機上 st.data_editor 內嵌編輯很難點,填代號按鈕即加（免動下方表格）。
    with st.form("quick_add_watch", clear_on_submit=True):
        st.markdown("**➕ 快速新增**（手機建議用這個：填代號 → 按新增）")
        fc1, fc2, fc3, fc4 = st.columns([1, 1.3, 1.6, 1])
        q_cat = fc1.selectbox("類別", ["台股", "ETF", "基金"], label_visibility="collapsed")
        q_code = fc2.text_input("代號", placeholder="代號 例 2317", label_visibility="collapsed")
        q_kw = fc3.text_input(
            "名稱/關鍵字", placeholder="名稱或新聞關鍵字（選填）", label_visibility="collapsed"
        )
        if fc4.form_submit_button("➕ 新增", use_container_width=True):
            _add_watch_item(q_cat, q_code, q_kw, store, owner)

    edited = st.data_editor(
        watchlist_to_df(_watchlist()),
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "類別": st.column_config.SelectboxColumn(options=["台股", "ETF", "基金"]),
            "權重": st.column_config.NumberColumn(min_value=0.0, max_value=1.0, step=0.01, format="%.2f"),
            "Sharpe": st.column_config.NumberColumn(step=0.1, format="%.2f"),
        },
        key="watchlist_editor",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 套用清單"):
            st.session_state.watchlist = watchlist_from_df(edited)
            if store is not None and owner:
                try:
                    store.set(owner, st.session_state.watchlist)
                    st.success(f"已存到 GitHub（{len(st.session_state.watchlist)} 檔）,雲端 / NAS 共用。")
                except SubscriberStoreError as exc:
                    st.error(f"存 GitHub 失敗：{exc}")
            else:
                st.success(f"已更新（{len(st.session_state.watchlist)} 檔,僅本次 session）。")
    with c2:
        if store is not None and owner and st.button("🔄 從 GitHub 重載"):
            try:
                st.session_state.watchlist = store.get(owner) or []
                st.success("已從 GitHub 重新載入清單。")
            except SubscriberStoreError as exc:
                st.error(f"重載失敗：{exc}")
    st.caption("⚠️ 線上 demo 只有 2330 / 2454 有資料;加自己的標的需接真實三庫才有數。")


def _tab_bullish(orch: WorkflowOrchestrator) -> None:
    st.subheader("📈 目前利多標的")
    items = _watchlist()
    if not items:
        st.info("清單為空,請先到『追蹤清單』新增。")
        return

    results = orch.run_batch([_request(it) for it in items])
    ranked = bullish_ranked(results)

    if not ranked:
        st.info("目前清單無利多訊號（強烈買進 / 適度加碼）。")
    else:
        st.markdown(f"**{len(ranked)} 檔利多**（依 Final Score 由高到低）：")
        for i, r in enumerate(ranked, 1):
            d = r.decision
            st.markdown(
                f"{i}. 🟢 **{d.tw_stock_id}** — {d.action.value} · Final `{d.final_score:.3f}`"
            )

    st.divider()
    st.markdown("#### 📤 LINE 推播")
    pusher = LinePusher(_secret("LINE_CHANNEL_ACCESS_TOKEN"), _secret("LINE_TO"))
    if not pusher.is_configured:
        st.caption(
            "未設定 LINE。於 App → Settings → Secrets 填 `LINE_CHANNEL_ACCESS_TOKEN` 與 "
            "`LINE_TO`（可為 `broadcast` / 多個 ID / 單一 ID，沿用 mynews 慣例）。"
        )
    if st.button("推播利多到 LINE", disabled=not pusher.is_configured):
        try:
            pusher.push_text(format_bullish_digest(results))
            st.success("已推播利多摘要到 LINE。")
        except LinePushError as exc:
            st.error(f"LINE 推播失敗：{exc}")
    with st.expander("預覽推播內容"):
        st.code(format_bullish_digest(results))


def _tab_detail(orch: WorkflowOrchestrator) -> None:
    st.subheader("🔍 個股決策")
    items = _watchlist()
    if not items:
        st.info("清單為空,請先到『追蹤清單』新增。")
        return
    labels = {f"{it.tw_stock_id}（{it.category}）": it for it in items}
    label = st.selectbox("選擇標的", list(labels))
    render_cycle_result(orch.run_once(_request(labels[label])))


def main() -> None:
    st.set_page_config(page_title="多智能體投研訊號", page_icon="🧠", layout="wide")
    st.title("🧠 多智能體投研訊號面板")
    st.caption("追蹤台股 / ETF / 基金 → 6 個 agent 分析 → 利多篩選 → LINE 推播（模擬情境）")

    orch = _get_orchestrator()
    tab1, tab2, tab3 = st.tabs(["📋 追蹤清單", "📈 利多標的", "🔍 個股決策"])
    with tab1:
        _tab_watchlist()
    with tab2:
        _tab_bullish(orch)
    with tab3:
        _tab_detail(orch)


if __name__ == "__main__":
    main()
