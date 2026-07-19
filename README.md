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

## 測試

```bash
pytest          # 59 個測試：單元 + 邊界 + 端到端
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
├── main.py                       # Demo 入口
├── multi_agent_system/
│   ├── contracts.py              # dataclasses + Action enum
│   ├── numerics.py               # clamp / linear_map / Sharpe
│   ├── data_agent.py             # ① 資料代理人
│   ├── macro_agent.py            # ② 總經專家
│   ├── macro_providers.py        #    總經來源介面 + 模擬/注入
│   ├── technical_agent.py        # ③ 技術專家
│   ├── allocation_agent.py       # ④ 配置專家
│   ├── strategy_agent.py         # ⑤ 策略融合
│   └── integration_agent.py      # ⑥ 編排 + Mock 券商
├── scripts/seed_demo_dbs.py      # 產生示範資料庫
└── tests/                        # pytest 測試
```
