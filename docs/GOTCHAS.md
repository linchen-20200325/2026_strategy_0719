# GOTCHAS — 踩坑筆記（行為保留式重構）

> 漸進揭露（Progressive Disclosure）：**做特定重構前才讀對應段落**，別整份背。
> 每條格式：**情境 → 坑 → 退路（Fallback）**。來源：2026-07 深層瘦身（Phase 0–3，13 commits）。

## 測試 / monkeypatch
- **移動 I/O 到共用模組 → 測試的 patch target 也要搬。** 例：`urlopen` 從 `line_push` 移到
  `infra/http` 後，`monkeypatch.setattr("multi_agent_system.line_push.urllib.request.urlopen", …)`
  失效。**退路**：共用模組用 `urllib.request.urlopen`（模組屬性，非 `from urllib.request import
  urlopen`）→ patch 全域仍生效；並把測試 target 改指新模組。
- **改前先 grep 測試斷言的「值」**。exact-value 斷言（`== pytest.approx(0.8133)`、`"401" in str`）
  決定重構能否 byte-identical。**退路**：保留原輸出格式 / 錯誤訊息含關鍵值（如 status code）。

## SSOT 收攏
- **先分辨「真·同一原語」vs「顯示用分母」。** `weighted_mean` 是融合均值；但
  `view_model.score_breakdown` / `strategy._build_rationale` 的 `total_w` 是**逐列權重分解的分母**，
  不是均值 —— 強收會改結構/行為。**退路**：只收語意完全相同者，其餘留註解說明為何不收。
- **float 細節**：`fsum(w·v)/fsum(w)`（fsum=1.0）vs 直接加總，差 ~1e-16；`approx(abs=1e-3)` 安全，
  但別對 exact 值假設。renorm 分支 `x/x` 恆等 1.0（安全）。
- **`replace_all "_x("` 前先移除 def**，否則 def 簽章也被改名。順序：先刪 def → 再 replace_all 呼叫點。

## DTO / 分層
- **搬 DTO 到 `contracts`(L0) 前，確認它不參照 L1+。** `CycleResult` 純→可搬；`ResearchRequest`
  參照 `MacroDataProvider`(L1) → **不能**下沉 L0。**退路**：留在原層，或連介面 Protocol 一起搬。
- **`market_digest` 用 duck-typing 吃 `CycleResult`**（`r.decision`，型別註解才 import）。grep 符號名會漏，
  **要 grep `import`**。
- **刪 dataclass field 後**，`field`/`datetime`/`timezone` import 可能變孤兒。**退路**：`ruff --fix` 抓 F401。
- **私有跨模組 import**（`from .data_agent import _connect_readonly`）是味道 → 抽**公開** `infra/` 模組；
  舊位置留 re-export 保向後相容（`from .data_agent import DataSourceError` 仍可用）。

## 工具 / 流程
- **`ruff --fix` 是大改後的清道夫**：自動排序 import(I001) + 刪未用(F401)。大量搬移後跑一次省手工，
  但**跑完務必再 `ruff check .` + 全測**確認沒改壞。
- **每階段獨立 commit + 每步跑全測（285 綠才 commit）** 是護城河 —— 出錯能精準二分定位。
- **container 是 ephemeral**：階段性 `git push` 保命（本地 commit 不夠）。

## 領域語意（別「修」成 bug）
- **NAS webhook reply 失敗只 warn 不 raise 是刻意的**：raise 會回 500 → LINE 重試風暴。看到
  「靜默吞錯」先問是不是 webhook 語意，別當 bug 修。
- **基金/個股 NAV 週末缺值正常，別 ffill**；模擬總經帶 `is_simulated` 排除計分（§1 Fail-Loud）。
