# LINE 盯盤 webhook（`scripts/nas_line_bot.py`）部署

讓好友在 LINE 上**自選**要盯的標的（加/刪/清單），寫進**同一個** `subscribers.json`，
排程端 `run_pipeline.py --per-user` 下一輪就逐人推他自己的利多。

```
好友打「加 2330」→ LINE 平台 POST /callback → 驗簽 → 改 subscribers.json → reply 回清單
                                                              │
                          cron: run_pipeline.py --per-user ───┘ 讀同一份 → 逐人 push
```

## 1. LINE Developers Console

1. 用**推播用的那個** Messaging API channel（好友加的就是它）。
2. **Messaging API** 分頁 → **Channel access token**：即 `LINE_CHANNEL_ACCESS_TOKEN`（推播已在用，reply 沿用同一個）。
3. **Basic settings** 分頁 → **Channel secret**：即 `LINE_CHANNEL_SECRET`（本 bot 驗簽用，**必填**）。
4. **Messaging API** → **Webhook URL** 填 `https://<你的網域>/callback`，開啟 **Use webhook**；關閉「自動回覆訊息」。

> Webhook URL 必須是 **HTTPS 對外可達**。NAS 一般用 DSM 反向代理 / 路由器 port forward，把外部
> `https://<網域>/callback` 導到這台的 `STRATEGY_BOT_PORT`（預設 8090）。

## 2. 環境變數

| 變數 | 必填 | 說明 |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | ✅ | reply / push 共用的 channel token |
| `LINE_CHANNEL_SECRET` | ✅ | 同一 channel 的 secret，驗 `X-Line-Signature` |
| `STRATEGY_BOT_TOKEN` | — | 想用**另一個** OA 當 bot 才設，會蓋掉 reply token |
| `STRATEGY_BOT_PORT` | — | 監聽埠，預設 `8090`（避開 mynews bot 的 8080） |
| `SUBSCRIBERS_FILE` | — | 清單路徑，預設 `subscribers.json`（要跟 `run_pipeline --subscribers` 一致） |
| `STRATEGY_ALLOW_USER` | — | bootstrap 授權名單（逗號/空白分隔）；**留空 = 對外開放**（誰都能加自己的清單） |
| `STRATEGY_ADMIN_USER` | — | 管理員 userId；未設則沿用 `STRATEGY_ALLOW_USER` |
| `STRATEGY_ALLOW_FILE` | — | 授權名單持久化檔，預設 `bot_allow.json`（管理員用 LINE 加人免重啟） |

## 3. 啟動

```bash
cd /path/to/2026_strategy_0719
LINE_CHANNEL_ACCESS_TOKEN=xxx \
LINE_CHANNEL_SECRET=yyy \
STRATEGY_ADMIN_USER=U你的userId \
python scripts/nas_line_bot.py
```

健檢：瀏覽器打 `http://<這台>:8090/` 應回 `strategy watch bot ok`。

### systemd（開機常駐，建議）

```ini
# /etc/systemd/system/strategy-line-bot.service
[Unit]
Description=Strategy LINE watch bot
After=network-online.target

[Service]
WorkingDirectory=/path/to/2026_strategy_0719
Environment=LINE_CHANNEL_ACCESS_TOKEN=xxx
Environment=LINE_CHANNEL_SECRET=yyy
Environment=STRATEGY_ADMIN_USER=U你的userId
# 與 deploy/crontab.example 的 --subscribers 路徑保持一致（同一份清單:webhook 寫、cron 讀）
Environment=SUBSCRIBERS_FILE=/volume1/data/subscribers.json
ExecStart=/usr/bin/python3 scripts/nas_line_bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now strategy-line-bot
```

## 4. 使用流程（「由你來加 id」怎麼運作）

1. 好友加你的 LINE 官方帳號 → 傳 **`id`** → bot 回他那串 `U…` userId。
2. 好友把 userId 貼給你（管理員）。
3. 你在 LINE 傳 **`授權 <那串id> 名字`**（或先把 id 放進 `STRATEGY_ALLOW_USER`）。
4. 好友傳 **`加 2330 台積電`** / `加 ETF 0050` / `刪 2330` / `清單` 自己維護清單。
5. cron 每天早盤前 / 收盤後跑 `run_pipeline.py --per-user` → 各自收到自己清單的利多。

> **開放 vs 授權**：`STRATEGY_ALLOW_USER` 與 `bot_allow.json` 都空 = 對外開放（任何加好友的人都能建立自己的清單）。
> 想只給特定人用，就設 `STRATEGY_ADMIN_USER`（你自己），再用 LINE「授權」逐一開通。

## 5. 安全 / 邊界（對照 Fail-Loud）

- 缺 `LINE_CHANNEL_SECRET` / token → **拒絕啟動**（exit 2），不裸奔。
- `X-Line-Signature` 驗不過 → 忽略該 webhook（擋偽造請求）。
- 寫 `subscribers.json` 失敗 → **回錯誤訊息**給使用者，不靜默吞。
- 單則訊息處理丟例外 → 該則回通用錯誤，服務續跑（不被一則打掛）。
- 未授權者 → 只會拿到「把 id 給管理員」提示，**不寫入**清單。
