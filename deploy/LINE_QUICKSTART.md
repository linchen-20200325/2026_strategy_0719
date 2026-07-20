# LINE 上線 checklist —— 從「程式已合併」到「能互動 + 收訊息」

程式全在 main 了,但 LINE **不會自己動**,要在你機器上啟動。這份照順序做即可。
分三級,難度由低到高;**只想先收到推播 → 做完 Level 1 就行**。

---

## 前置：LINE Developers Console 拿 3 個值

1. **Messaging API channel**（好友加的那個官方帳號 OA；沿用你 mynews 的 OA 也行）。
2. **Channel access token**（Messaging API 分頁）→ 環境變數 `LINE_CHANNEL_ACCESS_TOKEN`。
3. **Channel secret**（Basic settings 分頁）→ `LINE_CHANNEL_SECRET`（只有 Level 2 互動要用）。
4. **你的 userId**（`U` 開頭 33 碼）→ 對 bot 傳 `id` 取得，或用 mynews bot 的 `id` 指令。

> ⚠️ token / secret 一律走環境變數或 Secrets，**不要寫進程式或進版控**。

---

## Level 1 ── 先「收到推播」（最快，**不需對外網址**）

- [ ] 設 `LINE_CHANNEL_ACCESS_TOKEN`
- [ ] 驗一則真訊息（30 秒）：
      ```bash
      LINE_CHANNEL_ACCESS_TOKEN='你的token' LINE_TO='你的userId' \
      python -c "from multi_agent_system import LinePusher; LinePusher().push_text('測試 ✅')"
      ```
- [ ] 手機有收到 → 推播鏈路通
- [ ] NAS 裝 crontab（見 `deploy/crontab.example`）：
      - 市場快訊 broadcast：`run_pipeline.py --market-digest`
      - 個股利多 per-user：`run_pipeline.py --per-user`
- [ ] → **下一個 07:30 / 16:30（週一~五）就會自動推**

---

## Level 2 ── 能「互動」（你傳「加 2330」它回你）── 需要對外 HTTPS

LINE 要能**主動 POST 到你的機器**，所以 webhook bot 得常駐 + 對外可達。

- [ ] NAS 起 webhook：
      ```bash
      LINE_CHANNEL_ACCESS_TOKEN=xxx LINE_CHANNEL_SECRET=yyy \
      STRATEGY_ADMIN_USER=U你的userId python scripts/nas_line_bot.py
      ```
      （systemd 常駐寫法見 `deploy/nas_line_bot.md`）
- [ ] NAS 反向代理：對外 `https://<你的網域>/callback` → 本機 `:8090`
      （**照你 mynews 個股盯盤 bot 那套反代設定，換個埠即可** —— 你已經做過一次）
- [ ] LINE Console → Messaging API → **Webhook URL** 填 `https://<你的網域>/callback`，
      開 **Use webhook**、關「自動回覆訊息」
- [ ] 手機傳 `id` → 回你 userId；傳 `加 2330 台積電` → 建你的清單；`清單` → 列出
- [ ] → 你就能互動了

---

## Level 3 ── 讓推播「有真數據」（接三個 DB，收尾整條鏈）

沒接真 DB 前，線上只有 demo 的 2330/2454 有數。三個源專案各跑 export（都已合併進各自 main）：

- [ ] mynews（在下游 07:30 pull 前跑）：
      `NEWS_DB=/volume1/data/news.db python export_news_db.py`
- [ ] my-stock（需 `FINMIND_TOKEN` 才有技術面/月營收；離線層免 key）：
      `STOCK_DB=/volume1/data/stock.db FINMIND_TOKEN=xxx python scripts/export_stock_db.py`
- [ ] my-Fund（`us_market`/`fx` 需網路/proxy；離線層免 key）：
      `FUND_DB=/volume1/data/fund.db python scripts/export_fund_db.py`
- [ ] 2026 排程讀**同一組路徑**：`STOCK_DB` / `FUND_DB` / `NEWS_DB`（見 `deploy/crontab.example`）

> 排程順序：三個 export 排在下游 `run_pipeline` 之前（如 export 06:50、pipeline 07:30）。

---

## 環境變數總表

| 變數 | 用在哪 | 說明 |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | 推播 + webhook reply | 必備 |
| `LINE_CHANNEL_SECRET` | webhook 驗簽 | Level 2 必備 |
| `LINE_TO` | `--line` 群發 | `broadcast` / userId；`--market-digest` 強制 broadcast、不需此 |
| `WATCH_OWNER_ID` | dashboard 清單 | 你的 userId（雲端 dashboard 存清單用） |
| `STRATEGY_ADMIN_USER` | webhook | 管理員 userId（可用 LINE 授權他人） |
| `GITHUB_TOKEN` / `GITHUB_REPO` | 清單持久化 | 雲端+NAS 共用 subscribers.json（見 `deploy/shared_watchlist.md`） |
| `NEWS_DB` / `STOCK_DB` / `FUND_DB` | ETL + 下游 | 三庫共享路徑（兩端一致） |
| `FINMIND_TOKEN` | my-stock export | 技術面 / 月營收 / 景氣燈號 live 表 |
| `US_STOCK_IDS` | my-Fund export | 連動美股清單（預設 NVDA/AMD/…） |

---

## 誠實的門檻

**最大關卡 = Level 2 的「對外 HTTPS 網址」**。但你 mynews 的個股盯盤 bot 已經解決過這件事
（它也是 webhook、已對外可達、已驗收），所以照那套反代**換個埠**指到新 bot 即可 —— 你其實很近了。

**時程**：只收推播 → 今天/明天（設 token + cron）；能互動 → 你 NAS 反代弄好那天。
