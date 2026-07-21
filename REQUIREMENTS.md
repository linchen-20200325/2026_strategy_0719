# 需求規格 — 多智能體虛擬投研與自動交易系統

> 一句話：把 3 個來源專案（mynews / my-stock-dashboard / my-Fund-dashboard）產出的**資料**，
> 交給 `2026_strategy_0719` 這個 **AI Agent 大腦**判讀，每天把「國際盤快訊」與「個股盯盤」
> 結果推到 **LINE**。

---

## §0. 最高原則（凌駕一切）

1. **來源只出「資料」，判斷全在 2026**
   mynews / my-stock / my-Fund 只負責產出**原始資料**（新聞、台股指標、美股/全球總經、
   個股財報/技術原始值）。**所有評分、解讀、判斷一律在 2026 做**——2026 = AI Agent，
   來源專案**不做判斷、不外包評分回來源**。

2. **SSOT + 分層**：常數集中一處、判斷不外包、demo 資料物理隔離、跨層不亂 import。

3. **Fail-Loud, Never Fake**：缺資料一律誠實標示（「資料不足 / 未落地 / 模擬」），
   不造假、不填 0、不吞例外。

4. **安全**：金鑰只從環境變數讀、不進版控；log 不印 token / 完整 userId。

---

## §1. 系統架構（資料流）

```
mynews    ── export ──► news.db  ─┐  (各自 GitHub Actions 每日 export 到自己的 data 分支)
my-stock  ── export ──► stock.db ─┼─► 2026 checkout 三個 data 分支
my-Fund   ── export ──► fund.db  ─┘        │
                                    多智能體判讀（規則式，非 LLM）
                                           │
                                  LINE broadcast（國際盤）+ push（個股盯盤）
```

- 全程雲端（GitHub Actions），**無 NAS、無互動門**即可跑推播。
- 互動 bot 另在 NAS 常駐（見 §5）。

---

## §2. 資料來源（來源只出資料）

| 類別 | 來源 repo / DB | 內容（原始資料） |
|---|---|---|
| 新聞 | mynews / news.db | 美股新聞 + 台股新聞（即時 RSS） |
| 美股/全球總經 | my-Fund / fund.db | FRED 利差(10Y-2Y)、CPI、SPX、VIX…（離線層免 key） |
| 台股總經 | my-stock / stock.db | PMI、外資買賣超、M1B/M2、加權指數 |
| 個股財報 | my-stock / stock.db | EPS / 營收 / 毛利率 / 淨利率、月營收 |
| 個股技術 | my-stock / stock.db | close / RSI / 布林軌 / MA20 / MA60 / KD / 三大法人籌碼 |
| 盤前期貨 | my-stock / stock.db | 台指期外資留倉（口）、台指夜盤收盤（→ 隔日開盤領先） |

---

## §3. 2026 判讀（判斷全在此）

判讀**全為規則式（非 LLM）**，判斷全在 2026：

### 3a. 規則式多智能體評分 → 利多 / 中性 / 利空 + Final Score（可重現、Fail-Loud）

| 專家 | 判斷內容 | 用到的來源資料 |
|---|---|---|
| 總經 | 美股/全球（利差 + CPI + 情緒） | fund.db 總經 + news 情緒 |
| 技術 | 布林%B + RSI + 均線排列 + KD + 三大法人籌碼（多因子） | stock.db 技術原始值 |
| 基本面 | 毛利率 + 淨利率 + 月營收 YoY | stock.db 財報 + 月營收 |
| 配置 | 部位權重 + Sharpe + 集中度風控 | 使用者投組現況 |

- 融合權重為 SSOT；缺資料就**重新歸一化**（選填專家缺 → 不 abstain、行為不變）。
- 基本面 / 技術 / 夜盤等**加厚判讀全在 2026**，來源不改。

### 3b. 為何不接 LLM 敘事（user 2026-07-21 決策：「不要 LLM 虛假的」）

- 曾實作「Gemini 讀總經+新聞 → 🧠 綜合解讀敘事」與「個股新聞 AI 濃縮」，但 user 明確要求
  **「不要 LLM 虛假的」** → 已整層移除（`ai_summary.py` + 兩條呼叫點 + workflow `GEMINI_API_KEYS`）。
- 現況：**判讀 100% 規則式 + DB 真實資料**，可重現、零 LLM 幻覺風險。新聞一律只顯示
  news.db **真實頭條標題**（不經 LLM、不改寫、不杜撰）。
- 數字 / 分數 / 利多中性利空全走 §3a 規則式；符合 Fail-Loud「寧可炸掉，不可造假」。

---

## §4. 推播（LINE）

**A. 國際盤快訊（broadcast，發全體好友）** — 每天盤前 / 收盤後定時：
- 國際情勢：美股/全球總經（利差、CPI）+ 外電情緒。
- 台股：PMI + 外資買賣超 + **盤前夜盤**（台指期外資留倉 + 台指夜盤 → 隔日開盤五分類）
  + 追蹤清單訊號統計 + 台股新聞情緒。

**B. 個股盯盤卡（per-user push，逐人）** — 每人自己的清單：
- 每檔：判讀（利多/中性/利空 + Final）+ 技術 + 籌碼 + **新聞頭條**（news.db 真實標題）+ **最新財報**。

---

## §5. 互動 Bot（NAS 常駐，與 mynews bot 分開）

- 好友在 LINE 自選要盯的標的，**影響隔天推播內容**。
- **單一 .py、只用標準庫**（http.server / urllib / hmac / json），NAS 裸 python3 常駐。
- 指令：`加 / 刪 / 清單`（per-user，需授權）、`id`（任何人）、`授權 / 撤銷 / 名單`（管理員）。
- **共用 watchlist.json**（`users` + `allow` 同一份），走 **GitHub Contents API**（GET sha → PUT sha）。
- HMAC-SHA256 驗簽、先回 200 再處理、reply API、`GET /callback` 健康檢查。
- 參考 mynews bot 的設定，但**是獨立程式、不共用她的**。

---

## §6. LLM 政策 — 不使用（user 2026-07-21：「不要 LLM 虛假的」）

- **本系統不接任何 LLM**。判讀、新聞呈現全走規則式 + DB 真實資料。
- 曾短暫實作 Gemini 解讀（總經+新聞）與個股新聞 AI 濃縮，已依 user 決策整層移除（見 §3b）。
- 未來若重評估 LLM，僅限「**文字輔助、且上最嚴約束（只准依給定數字解讀、禁止新增任何數字/事件）**」，
  且須先經 user 同意；**數字一律不得由 LLM 產生**（Fund EX-AI-1 精神）。

---

## §7. 判讀增強（對標 kevin801221/stock-strategies-only）

已做（全部「來源出資料、2026 判斷」，全規則式、無 LLM）：
- **A 基本面**進融合、**B 盤前夜盤**、**C 月營收動能**、**#2 技術加厚**（均線/KD/籌碼）。

尚未做（不急，等點名）：
- **D 回測勝率**（要 PIT-safe，先前刻意移除回測）。
- **出場 / 停損進決策**（my-stock 有 exit_signals/ATR，2026 決策尚未帶）。
- **db_contract.py**（跨 repo 表名/欄名 SSOT 收斂）。

---

## §8. 安全與治理

- 金鑰只從環境變數；PAT 支援 `GITHUB_TOKEN_FILE`；**不進版控**。
- log 不印 token / 完整 userId（只留前 8 碼）。
- 每個來源 repo 遵循自己的 `CLAUDE.md` 資料憲法（Fail-Loud / SSOT / 分層）。
- 判斷邏輯**不外流到來源 repo**；跨 repo 的 DB 表名為隱性契約。

---

## §9. 部署設定（Secrets / Variables）

| repo | Secret | 用途 |
|---|---|---|
| 2026 | `DATA_REPO_TOKEN` | PAT，讀三來源 repo 的 `data` 分支 |
| 2026 | `LINE_CHANNEL_ACCESS_TOKEN` | broadcast / push |
| 2026 webhook（NAS） | `LINE_CHANNEL_SECRET` | 驗簽 |
| my-stock / my-Fund / mynews | `FINMIND_TOKEN`（my-stock live 表）/ `PROXY_URL`（選） | export 抓資料 |

---

_本檔為需求規格快照，實作進度見各 repo 的 PR 與 commit。_
