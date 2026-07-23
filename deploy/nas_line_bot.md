# 投研盯盤 LINE Bot 架設說明書（2026 多智能體）

> 版本：v2.0（**設定方式參照 mynews `NAS_WATCH_BOT_SETUP.md`**，但為**獨立的一支 bot**）
> 適用：讓好友在 LINE 上即時加/刪自選標的，並每天早盤前/收盤後收到自選個股的
>       「判讀＋技術（收/20MA/60MA/KD/RSI）＋籌碼（外資/投信/三大法人）」盯盤卡。
> 對應程式：`scripts/nas_line_bot.py`（NAS 常駐 webhook）、`run_pipeline.py --per-user`（排程推播）、
>          `multi_agent_system/subscribers.py`（清單 store）。

> ⚠️ **這是一支「獨立的」bot**：**自己的 Messaging API channel（OA）**、**自己的埠 8090**。
> 它跟你 mynews 那支盯盤 bot（埠 8080）**設定方式一模一樣、但完全分開** —— 不共用 OA、不共用程式、
> 不共用埠。下面每一步都對照得到 mynews 的哪一步（見文末對照表）。

---

## 一、運作架構（為什麼一定要在 NAS 跑一支）

整套推播是「**單向排程**」（cron 讀 `subscribers.json` → 逐人 push），**沒有**能「**接收**」LINE
訊息的伺服器。GitHub Actions runner 跑完就銷毀、沒有固定網址，**收不到** LINE 打進來的 webhook。
所以要在 LINE 上加/刪股票，必須有一台 24h 在線的程式接 webhook —— 就跑在你的 NAS 上
（與 `run_pipeline.py` 的 cron 同一台）。

```
編輯清單（即時）：
  你在 LINE 打「加 2330」
        │  LINE 平台 POST /callback（帶 X-Line-Signature）
        ▼
  NAS：nas_line_bot.py（HMAC-SHA256 驗簽 → make_subscriber_store 改 subscribers.json）
        │  GITHUB_TOKEN + GITHUB_REPO → GitHub Contents API 寫回 repo（雲端 dashboard 也看得到）
        ▼
  repo/subscribers.json ✅  → bot reply 回你目前清單

每天早盤前 / 收盤後（推播）：
  NAS cron → run_pipeline.py --per-user
        │  讀同一份 subscribers.json → 逐人跑三庫判讀 → 產「個股盯盤卡」
        ▼
  同一支 OA push 給每位訂閱者本人（對象取自清單，免 LINE_TO）
```

**同一份 `subscribers.json`**：LINE 加/刪、雲端 dashboard、cron 推播三邊共用，不會漂移
（設 `GITHUB_TOKEN`+`GITHUB_REPO` 走 repo 內 JSON；沒設則退本機檔）。

---

## 二、前置：開**一支獨立的** LINE bot（第三支），拿三樣東西

在**電腦瀏覽器**進 [LINE Developers Console](https://developers.line.biz/console/)：

1. 建立（或沿用同一個 Provider）→ 底下**新建一個 Messaging API channel**（名字例如「投研盯盤」）。
   **不要**用 mynews 那兩支的 channel —— 這是新的第三支。
2. 取得三樣東西：

| 要的東西 | 在哪 | 設到哪 |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | **Messaging API** 分頁 → **Channel access token (long-lived)** → **Issue** | NAS 環境變數（bot reply + cron push 共用同一個 OA） |
| `LINE_CHANNEL_SECRET` | **Basic settings** 分頁 → **Channel secret** | **只放 NAS**（驗簽用，不進 repo） |
| 你的 `userId` | 加 bot 好友後傳「**id**」給它，它會回你 | `STRATEGY_ADMIN_USER`（你就是管理員） |

3. **Messaging API** 分頁用手機掃 **QR code** 加這支 bot 為好友（不加好友收不到推播）。
4. **回應設定**：回應模式改 **Bot**、**關掉**「自動回覆訊息 / 歡迎訊息」（否則官方罐頭訊息會蓋掉 bot 回覆——
   這正是你先前「傳 id 只收到罐頭」的原因）。

---

## 三、NAS 準備

### 3-1. 放程式
NAS 上若已 clone 本 repo（跑 cron 那台），直接 `git pull`；否則 clone 一份。
`nas_line_bot.py` 會自動把 repo 根目錄加進 import path，找得到 `multi_agent_system`。

### 3-2. 準備一個有寫入權的 GitHub Token（改 subscribers.json 用）
GitHub → Settings → Developer settings → **Fine-grained tokens** → 只授權**本 repo** 的
**Contents: Read and write**。存成檔案並鎖權限（**同 mynews：token 放檔案、不進 git**）：
```bash
echo 'github_pat_xxx' > /volume1/homes/<you>/.strategy_gh_token
chmod 600 /volume1/homes/<you>/.strategy_gh_token
```
> 本 repo 存的是 userId（同 mynews watchlist.json）→ **只能用私有 repo**。

### 3-3. 先手動試跑
```bash
LINE_CHANNEL_ACCESS_TOKEN='xxx' \
LINE_CHANNEL_SECRET='yyy' \
STRATEGY_ADMIN_USER='U你的userId' \
GITHUB_TOKEN_FILE='/volume1/homes/<you>/.strategy_gh_token' \
GITHUB_REPO='linchen-20200325/2026_strategy_0719' \
STRATEGY_BOT_PORT=8090 \
  /usr/bin/python3 /volume1/.../scripts/nas_line_bot.py
```
看到 `投研盯盤 webhook 啟動,監聽 :8090` 即正常。瀏覽器打 `http://<這台>:8090/` 應回
`strategy watch bot ok`。先別關，進下一步接上外網。

**可選環境變數：**
- `STRATEGY_ALLOW_USER`：bootstrap 授權名單（逗號/空白分隔）。留空＝對外開放（誰都能建自己的清單）。
- `STRATEGY_ADMIN_USER`：管理員 userId（可下 `授權`/`撤銷`/`名單`）；未設則沿用 `STRATEGY_ALLOW_USER`。
- 授權名單（allow）：與清單**存在同一份** subscribers JSON（repo 或本機，見 §六），管理員用 LINE `授權`/`撤銷` 即時增減、免重啟;**無獨立 allow 檔**（`store.allow_ids()`）。
- `SUBSCRIBERS_FILE`：本機清單路徑（沒設 GITHUB_* 時用），需與 cron `--subscribers` 一致。
- `STRATEGY_BOT_TOKEN`：想用**另一個** OA 當 bot 才設，會蓋掉 reply token（一般不用）。
- `GITHUB_BRANCH`：預設 `main`。

---

## 四、把 NAS 對外（LINE 要打得進來）

LINE webhook 必須是 **HTTPS 公開網址**。**照你 mynews 那套換個子網域即可**（你已做過一次）。二選一：

### 方案 A：Cloudflare Tunnel（推薦，免開埠、免固定 IP、自帶 HTTPS）
```bash
cloudflared tunnel login
cloudflared tunnel create strategy-watch
# config.yml：把 strategy.<你的網域> 導到 http://localhost:8090
cloudflared tunnel route dns strategy-watch strategy.<你的網域>
cloudflared tunnel run strategy-watch
```
Webhook URL = `https://strategy.<你的網域>/callback`
> 用**不同子網域**（`strategy.` 而非 mynews 的 `watch.`），兩支 bot 各走各的。

### 方案 B：路由器埠轉發 + DDNS（Synology 內建）
DSM → 外部存取 → DDNS 設 `xxx.synology.me`＋憑證；用 DSM **反向代理**把
`https://xxx.synology.me/strategy-callback`（或另一個 DDNS）導到 `localhost:8090`。
> mynews 的反代指 8080，這支指 **8090**，兩條反代規則並存、不衝突。

**驗證連線**：瀏覽器開 `https://.../callback`（GET）應看到 `strategy watch bot ok`。

---

## 五、回 LINE Console 設 Webhook

新那支 channel 的 **Messaging API** 分頁 → **Webhook settings**：
1. **Webhook URL** 填上面的 `https://.../callback`。
2. 開啟 **Use webhook**。
3. 按 **Verify** → 應回 Success（失敗見第八節）。

---

## 六、清單共享 + 排程推播（讓早盤前/收盤後會推盯盤卡）

清單三邊同源（設 `GITHUB_TOKEN[_FILE]`+`GITHUB_REPO` 即生效，見 `deploy/shared_watchlist.md`）：
```
雲端 dashboard 編清單 ┐
LINE「加 2330」        ├─► subscribers.json（repo）
CLI                    ┘            │
        cron: run_pipeline --per-user ──讀同一份──┘ 逐人推盯盤卡
```
排程見 `deploy/crontab.example`（已含 export→pipeline 完整時序）：
- `--per-user`：逐人**個股盯盤卡**（判讀＋技術＋籌碼）
- `--market-digest`：國際情勢＋台股快訊 broadcast
> 兩者與本 webhook **共用同一支 OA**（`LINE_CHANNEL_ACCESS_TOKEN`）——使用者眼中就是一條連續對話。

---

## 七、Synology 開機自動啟動（讓 webhook 常駐）

DSM → **控制台 → 任務排程 → 新增 → 觸發的任務 → 使用者定義指令碼**：
- 一般：使用者 `root`、事件 **開機**。
- 執行指令：
  ```bash
  LINE_CHANNEL_ACCESS_TOKEN='xxx' \
  LINE_CHANNEL_SECRET='yyy' \
  STRATEGY_ADMIN_USER='U你的userId' \
  GITHUB_TOKEN_FILE='/volume1/homes/<you>/.strategy_gh_token' \
  GITHUB_REPO='linchen-20200325/2026_strategy_0719' \
  STRATEGY_BOT_PORT=8090 \
    /usr/bin/python3 /volume1/.../scripts/nas_line_bot.py >> /volume1/.../strategy_bot.log 2>&1
  ```
> Cloudflare Tunnel（方案 A）也照樣設一個開機任務跑 `cloudflared tunnel run strategy-watch`。

**（非 Synology 的替代：systemd）** 建 `/etc/systemd/system/strategy-line-bot.service`
（`ExecStart` 同上、`Restart=always`），`sudo systemctl enable --now strategy-line-bot`。

---

## 八、驗收 & 疑難排解

**驗收**：手機 LINE 傳給「投研盯盤」bot：
- `id` → 回你的 userId（任何人都會回，貼給管理員授權）
- `加 2330 台積電` → 回「✅ 已加入…」＋目前清單（需先被授權）；`加 ETF 0050` / `加 基金 <code>`
- `清單` → 列出**你自己**的清單（per-user）
- `刪 2330` → 移除一檔

**管理員指令**（`STRATEGY_ADMIN_USER` / bootstrap 名單內的人，即時生效免重啟）：
- `授權 <對方userId> [名字]` / `撤銷 <對方userId>` / `名單`

| 症狀 | 多半原因 |
|---|---|
| 傳訊息 bot **不回**、只收到罐頭 | 「自動回覆」沒關 / 回應模式不是 Bot（第二節第 4 步） |
| Console **Verify 失敗** | NAS 沒對外 / 網址打錯 / bot 沒在跑；先用瀏覽器 GET `/callback` 確認回 `strategy watch bot ok` |
| bot 回但**驗簽失敗** | `LINE_CHANNEL_SECRET` 填錯（看 NAS log） |
| 回「**清單更新失敗**」 | `GITHUB_TOKEN` 沒有本 repo 的 **Contents: write**，或 `GITHUB_REPO`/branch 設錯 |
| 早上/收盤後**沒推盯盤** | 當天清單為空 / cron 沒跑 / `LINE_CHANNEL_ACCESS_TOKEN` 沒設（見 crontab.example） |

---

## 九、安全須知
- `LINE_CHANNEL_SECRET`、GitHub token（`GITHUB_TOKEN_FILE`）**只放 NAS**（chmod 600），**切勿進 git**。
- 建議設 `STRATEGY_ALLOW_USER`，避免陌生人對你的 webhook 亂改清單。
- `subscribers.json` 含 userId → **僅適用私有 repo**。
- 所有推播內容均為工具自動生成，**僅供參考，非投資建議**。

---

## 附：與 mynews `NAS_WATCH_BOT_SETUP.md` 的對照（同一套設定，不同 bot）

| 用途 | mynews 盯盤 bot | 2026 投研盯盤 bot（本檔） |
|---|---|---|
| bot channel token | `LINE_WATCH_TOKEN` | `LINE_CHANNEL_ACCESS_TOKEN`（reply＋cron push 共用） |
| channel secret（驗簽） | `LINE_WATCH_SECRET` | `LINE_CHANNEL_SECRET` |
| 監聽埠 | `WATCH_BOT_PORT`（8080） | `STRATEGY_BOT_PORT`（**8090**） |
| bootstrap 授權 | `WATCH_ALLOW_USER` | `STRATEGY_ALLOW_USER` |
| 管理員 | `WATCH_ADMIN_USER` | `STRATEGY_ADMIN_USER` |
| GitHub token 檔 | `GITHUB_TOKEN_FILE` | `GITHUB_TOKEN_FILE`（**相同**） |
| repo / branch | `GITHUB_REPO` / `GITHUB_BRANCH` | `GITHUB_REPO` / `GITHUB_BRANCH` |
| 清單檔 | `watchlist.json` | `subscribers.json` |
| 對外子網域 | `watch.<domain>` | `strategy.<domain>` |
| 排程推播對象 | `LINE_WATCH_TO`（secret） | 取自 `subscribers.json`（逐人，免 LINE_TO） |

> 一句話：**照你架 mynews 那支的每一步做，只是換成上表右欄的名字＋新的 OA＋埠 8090。**
