# 多智能體虛擬投研與自動交易系統 (Multi-Agent Trading System)

以 **1 個資料代理人 + 5 個虛擬專家智能體**，跨接三個在地化 SQLite 資料庫
（`stock.db` / `fund.db` / `news.db`），完成「跨庫取數 → 多專家評分 → 決策融合 →
五大交易行動 → Mock 下單」的完整投研工作流。

> 設計信條（承襲三個 dashboard 專案的資料憲法）：**Fail Loud, Never Fake** —
> 寧可大聲炸掉，也不用假資料掩蓋。缺料一律帶旗標 abstain，模擬值一律標記 `is_simulated`。

---

## 系統架構

```
                      ┌──────────────────────────────────────────────┐
   stock.db  ───────► │  ① 資料代理人 DataAggregationAgent            │
   fund.db   ───────► │     跨三庫查詢 → 標準 DataPacket (JSON)       │
   news.db   ───────► └──────────────────────────────────────────────┘
                                        │  DataPacket
                 ┌──────────────────────┼───────────────────────┐
                 ▼                      ▼                        ▼
   ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐
   │ ② 總經專家 Macro    │  │ ③ 技術專家 Technical│  │ ④ 配置專家 Allocation│
   │  10Y-2Y / CPI / 情緒│  │  布林 %B + RSI      │  │  MPT / Sharpe / 風控 │
   └─────────┬──────────┘  └─────────┬──────────┘  └─────────┬──────────┘
             │ 0.30                   │ 0.50                   │ 0.20
             └────────────────────────┼────────────────────────┘
                                       ▼
                         ┌────────────────────────────┐
                         │ ⑤ 策略專家 Strategy (大腦)   │
                         │  決策融合 → Final Score → 五大│
                         │  行動 + 風控硬約束           │
                         └──────────────┬─────────────┘
                                        ▼
                         ┌────────────────────────────┐
                         │ ⑥ 系統整合 Orchestrator      │
                         │  定時/定點觸發 + Mock 下單    │
                         └────────────────────────────┘
```

| # | 智能體 | 職責 | 對應檔案 |
|---|--------|------|----------|
| ① | 資料代理人 | 跨三庫查最新技術面/美股連動/近 7 天新聞，打包 JSON | `multi_agent_system/data_agent.py` |
| ② | 總經專家 | 評估系統性風險（殖利率倒掛 + CPI + 新聞情緒）→ 健康度 [0,1] | `multi_agent_system/macro_agent.py` |
| ③ | 技術線型專家 | 布林 %B + RSI 判斷超買/超賣 → 技術面得分 [0,1] | `multi_agent_system/technical_agent.py` |
| ④ | 資產配置專家 | MPT / Sharpe，並對集中度超限強制發出風控減碼信號 | `multi_agent_system/allocation_agent.py` |
| ⑤ | 策略專家 | 30/50/20 加權融合 → 五大行動 + 風控硬約束 | `multi_agent_system/strategy_agent.py` |
| ⑥ | 系統整合專家 | 主工作流編排 + 定時觸發 + Mock 券商下單介面 | `multi_agent_system/integration_agent.py` |

---

## 金融原理與計算式（摘要）

**殖利率曲線（總經）**
```
spread_pct  = yield_10Y(%) - yield_2Y(%)
curve_score = clamp( spread_pct / 1.5, 0, 1 )      # <=0 倒掛=0 分；>=1.5% =1 分
```

**布林通道 %B（技術）**
```
upper = SMA(n) + k·σ,  lower = SMA(n) - k·σ         # n=20, k=2
%B    = (close - lower) / (upper - lower)           # 0 貼下軌(便宜) / 1 貼上軌(貴)
cheapness_%B = 1 - clamp(%B, 0, 1)
```

**RSI（技術）**
```
RSI = 100 - 100 / (1 + 平均漲幅/平均跌幅)            # ∈ [0,100]
cheapness_RSI = clamp( (70 - RSI) / (70 - 30), 0, 1 )
```

**夏普比率（配置）**
```
Sharpe_annual = (E[R_p] - R_f) / σ_p · sqrt(252)
sharpe_score  = clamp( Sharpe / 2.0, 0, 1 )
```
集中度風控：`current_weight > max_weight` → `alloc_score = 0.25·(1 - clamp(overshoot,0,1))`，
並回傳 `risk_control_triggered=True`。

**決策融合（策略）**
```
Final = 0.30·S_macro + 0.50·S_tech + 0.20·S_alloc
Final >= 0.80 強烈買進 | >=0.60 加碼 | >=0.40 觀望 | >=0.20 減碼 | <0.20 強烈賣出
風控觸發時，行動不得比「適度減碼」更偏多（hard override）。
```

---

## 安裝

```bash
pip install -r requirements.txt
```
（`sqlite3` 為 Python 標準庫，無需安裝。）

## 快速開始（可直接跑通）

```bash
python main.py
```
`main.py` 會自動在 `demo_data/` 建立三個示範資料庫，並跑三個代表性情境：
多頭強買、集中度風控強制賣出、資料不足 abstain。

## 對接真實資料 / 券商

```python
from multi_agent_system import (
    DataAggregationAgent, WorkflowOrchestrator, ResearchRequest,
    StaticMacroProvider, default_portfolio_state,
)

# 1) 指向你的真實資料庫（表名可用 stock_table/us_table/news_table 覆寫）
agent = DataAggregationAgent("path/stock.db", "path/fund.db", "path/news.db")

# 2) 注入「真實」總經數值（例如已從 FRED 抓好；is_simulated 預設 False）
macro = StaticMacroProvider(yield_spread_pct=0.85, cpi_yoy_pct=3.1, as_of="2026-07-19")

orch = WorkflowOrchestrator(agent)   # 預設 MockBrokerAPI，永不真實成交
result = orch.run_once(ResearchRequest(
    tw_stock_id="2330", us_stock_id="NVDA",
    news_keywords=["台積電", "半導體"],
    portfolio_state=default_portfolio_state(0.10, sharpe=1.4),
    macro_provider=macro,
))
print(result.decision.summary())
```

* **真實券商**：實作 `BrokerAPI` 介面（`place_order(symbol, side, quantity)`）後傳入
  `WorkflowOrchestrator(..., broker=YourBroker())`。
* **真實 FRED**：於 `FredMacroProvider.get_reading()` 實作 HTTP 抓取（骨架已備；未接線時 raise）。
* **定時觸發**：`orch.run_scheduled(reqs, interval_sec=..., max_iterations=...)`；
  生產環境建議改用 cron / APScheduler。

## 視覺化 / 通知元件（Streamlit）

決策結果可用 **視覺化圖表 + 通知小元件** 無縫嵌入既有 Streamlit dashboard。
配色鏡像既有 dashboard 的 traffic-light 主題（`shared/colors.py`），並支援明暗主題。

**一鍵嵌入**（把這一行放進你 dashboard 的任一 tab 即可）：

```python
from multi_agent_system.ui import render_cycle_result
render_cycle_result(orchestrator.run_once(request))   # 徽章 + 得分圖 + 血緣 + Mock 下單
```

**通知中心小元件**（多標的訊號摘要，適合放 sidebar / 頂端）：

```python
from multi_agent_system.ui import render_notification_center
render_notification_center(results, only_actionable=True)   # 只顯示買賣訊號，略過 Hold/abstain
```

**獨立展示頁**：

```bash
streamlit run dashboard.py
```

元件清單（`multi_agent_system/ui/`）：`render_signal_badge` / `render_score_breakdown` /
`render_provenance` / `render_mock_order` / `render_decision_panel` / `render_cycle_result` /
`render_notification_center`。核心 agents **不** import streamlit（分層隔離，cron/測試可獨立跑）。

**通知管道**：`Notifier` 介面現有 `ConsoleNotifier` / `StreamlitToastNotifier` / **`LineNotifier`（LINE 推播，已實作）**。
LINE 走 Messaging API push、標準庫 `urllib`（無新相依、支援代理），詳見下方「排程執行」。

## 排程執行（早上 / 下午）

`run_pipeline.py` 讀三庫 → 跑 agents → 通知 → 產出 JSON 報告，設計給 cron / GitHub Actions 定時呼叫。
執行前先跑 **新鮮度守門**（Fail-Loud）：三庫最新資料距今 > `max_age_days`（預設 4，涵蓋週末）即告警；
加 `--strict` 則直接中止，避免 AI 跑在舊資料上。

```bash
export STOCK_DB=/path/stock.db FUND_DB=/path/fund.db NEWS_DB=/path/news.db
python run_pipeline.py --session morning                                   # 盤前場
python run_pipeline.py --session afternoon --strict --output signals.json  # 收盤後場
python run_pipeline.py --session morning --demo                            # 示範（自動 seed）
python run_pipeline.py --session afternoon --line                          # 收盤後 + LINE 推播
```

**LINE 推播**（`--line`）：一輪推**一則彙整訊息**（含新鮮度 + 各檔訊號），不逐檔洗版。
走 LINE Messaging API push（LINE Notify 已停用），設兩個環境變數即可：

```bash
export LINE_CHANNEL_ACCESS_TOKEN=<Messaging API channel 的長期 token>
export LINE_TO=<推播對象 userId / groupId>
python run_pipeline.py --session afternoon --line
```
未設定卻加 `--line` → 明確報錯並回傳非 0（Fail-Loud，方便監控）。程式化用法：
`from multi_agent_system import LineNotifier`（實作 `Notifier`，與 Console 可互換）。

**建議時點（台灣時間，週一至週五）**：

| 場次 | TW | UTC | 用途 |
|---|---|---|---|
| 早上 | 07:30 | 前一日 23:30 | 盤前：前一日收盤 + 隔夜美股(fund.db) + 隔夜新聞 → 當日計畫 |
| 下午 | 16:30 | 08:30 | 收盤後：當日已結算收盤 → 收盤後調整 |

> ⚠️ 下午場需在 `stock.db` 已更新「當日收盤」之後才跑；若你的 stock.db 較晚更新（如 17:00），
> 把下午場往後移。新鮮度守門會在資料過舊時告警。

**兩種排程方式**：
- **NAS / server crontab**（首選，DB 通常在本機）：見 `deploy/crontab.example`
- **GitHub Actions cron**（替代，需 runner 讀得到三個 DB）：見 `.github/workflows/run_pipeline.yml`

**總經數據**：設 `MACRO_SPREAD_PCT` + `MACRO_CPI_YOY_PCT` → 真實注入值；否則用模擬中性情境並印警語
（`is_simulated=True`，接 FRED 後改真實值）。

## 測試

```bash
pytest          # 90 個測試：單元 + 邊界 + 端到端 + Streamlit AppTest + 排程/新鮮度
ruff check .    # lint
```

---

## 設計原則

* **Fail Loud, Never Fake**：缺料 abstain 帶旗標；模擬總經標記 `is_simulated`；
  Sharpe 零波動 / 資料庫缺檔一律 raise，不回假 0。
* **SSOT**：所有門檻/權重集中於 `config.py`，agent 內零 inline magic number。
* **Provenance**：`DataPacket` 各段帶 `source` / `fetched_at` / `as_of`。
* **分層**：`config`（常數）→ `numerics`（純數值）→ 各 agent（純函式評分）→
  `integration`（I/O 編排）；資料庫一律唯讀開啟，杜絕污染上游。

## 目錄結構

```
2026_strategy_0719/
├── config.py                     # SSOT：權重 / 門檻 / 參數
├── paths.py                      # SSOT：資料落地路徑（別亂放資料，見下表）
├── main.py                       # CLI Demo 入口
├── dashboard.py                  # Streamlit 展示頁（streamlit run dashboard.py）
├── run_pipeline.py               # 排程 CLI（cron / Actions 呼叫）
├── multi_agent_system/
│   ├── contracts.py              # dataclasses + Action enum
│   ├── numerics.py               # clamp / linear_map / Sharpe
│   ├── notifications.py          # Notifier / Console（無 streamlit）
│   ├── line_push.py              # LINE 推播（Messaging API, stdlib urllib）
│   ├── data_agent.py             # ① 資料代理人
│   ├── macro_agent.py            # ② 總經專家
│   ├── macro_providers.py        #    總經來源介面 + 模擬/注入
│   ├── technical_agent.py        # ③ 技術專家
│   ├── allocation_agent.py       # ④ 配置專家
│   ├── strategy_agent.py         # ⑤ 策略融合
│   ├── integration_agent.py      # ⑥ 編排 + Mock 券商
│   ├── ledger/                   # forward-test 判讀對帳（append-only JSONL）
│   │   ├── store.py              #    大盤判讀持久化（落地走 paths SSOT）
│   │   ├── stock_store.py        #    個股判讀持久化（落地走 paths SSOT）
│   │   ├── reconcile.py          #    單筆對帳（open-to-open，無 lookahead）
│   │   └── report.py             #    stateless 聚合命中率 + 淨值
│   ├── pipeline/                 # 排程層（無 streamlit）
│   │   ├── watchlist.py          #    觀察清單 + DB 路徑（env）
│   │   ├── freshness.py          #    新鮮度守門（Fail-Loud）
│   │   └── runner.py             #    批次執行 + 通知 + 報告
│   └── ui/                       # 視覺化 / 通知層（streamlit，可選）
│       ├── theme.py              #    配色（鏡像 dashboard traffic-light）
│       ├── view_model.py         #    純轉換層（無 streamlit，可測）
│       ├── components.py         #    render 元件（徽章 / 圖表 / 通知中心）
│       └── notify.py             #    toast + re-export 核心 notifier
├── deploy/crontab.example        # NAS/server 排程範例
├── .github/workflows/run_pipeline.yml   # GitHub Actions 排程（替代）
├── scripts/seed_demo_dbs.py      # 產生示範資料庫
└── tests/                        # pytest（90 個：含 AppTest + 排程/新鮮度）
```

### 資料落地位置（SSOT：`paths.py`）

「別亂放資料」：執行期產生的檔案落地位置**只在 `paths.py` 定義一次**，以 repo 根為錨
（與 CWD 無關 → 從任何目錄跑都進同一個分類好的位置，不再散落到 repo root）。

| 類別 | 位置 | 定義處 | 入庫 |
|---|---|---|---|
| 本地 forward-test ledger | `data/`（`ledger.jsonl` / `stock_ledger.jsonl`） | `paths.DATA_DIR` | ❌ gitignore |
| 訂閱者清單（本機 fallback） | `data/subscribers.json`（既有 root 檔存在則沿用） | `paths.SUBSCRIBERS_FILE` | ❌ gitignore |
| 示範 DB（demo） | `demo_data/`（stock/fund/news.db） | `paths.DEMO_DATA_DIR` | ❌ gitignore |
| 來源 DB（真實） | env `STOCK_DB` / `FUND_DB` / `NEWS_DB` | `pipeline.watchlist.load_db_paths` | ❌（外部 data 分支） |

**覆寫順序**：顯式 `path=` 參數 > 環境變數（`LEDGER_FILE` / `STOCK_LEDGER_FILE`）> `paths.py` 預設。
CI（`run_pipeline.yml`）以 env 指定 bare 檔名並存回 **ledger 分支**（扁平 data 分支,刻意）；
本地不設 env → 落到 `data/`。兩者互不干擾。
