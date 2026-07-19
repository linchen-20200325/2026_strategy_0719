# 共享觀察清單（watchlist.json + GitHub 寫回，真·同 mynews）

讓**雲端 dashboard**、**NAS webhook**、**cron** 共用**同一份**清單 —— 存在你 repo 裡的
`subscribers.json`（GitHub Contents API 讀寫，就是 mynews `watchlist.json` 那招）。

```
雲端 dashboard「套用清單」┐
LINE webhook「加 2330」   ├─► GithubSubscriberStore ──PUT──► repo/subscribers.json
CLI subscribers_cli       ┘                                        │
                          cron: run_pipeline --per-user ──GET──────┘ 逐人 push
```

**零新套件**：只用標準庫 `urllib` + 你已有的 `GITHUB_TOKEN`（不裝 gspread/google）。

## 一、環境變數 / Secrets

| 變數 | 必填 | 說明 |
|---|---|---|
| `GITHUB_TOKEN` | ✅ | 對**本 repo** 有 `contents:write` 的 PAT（fine-grained 或 classic 皆可） |
| `GITHUB_REPO` | ✅ | `owner/name`，例：`linchen-20200325/2026_strategy_0719` |
| `GITHUB_BRANCH` | — | 預設 `main` |
| `SUBSCRIBERS_REPO_PATH` | — | 清單在 repo 的路徑，預設 `subscribers.json` |
| `SUBSCRIBERS_BACKEND` | — | 設 `github` 強制走 GitHub;設 `local` 強制走本機檔;留空 = 有上面 token+repo 就自動走 GitHub |
| `WATCH_OWNER_ID` | dashboard 用 | **你自己的 LINE userId** —— dashboard 這份清單掛在這個 user 底下（cron `--per-user` 才會推給你本人） |

> **backend 自動判斷**：設了 `GITHUB_TOKEN` + `GITHUB_REPO` → 自動走 GitHub 共享;沒設 → 退回本機 `subscribers.json`（純 NAS 單機也能用）。

## 二、三處怎麼設

**① Streamlit Cloud dashboard**（App → Settings → Secrets，TOML）：
```toml
GITHUB_TOKEN = "github_pat_xxx"
GITHUB_REPO  = "linchen-20200325/2026_strategy_0719"
WATCH_OWNER_ID = "Uxxxxxxxxxxxxxxxx"   # 你的 LINE userId
# 要在 dashboard 直接推 LINE 再加這兩個：
LINE_CHANNEL_ACCESS_TOKEN = "xxx"
LINE_TO = "broadcast"
```
設好後「📋 追蹤清單」分頁會顯示「✅ 已接 GitHub 持久化」，**加了就存得住、重整不消失**。

**② NAS webhook（`nas_line_bot.py`）與 ③ cron（`run_pipeline.py --per-user`）**：export 同一組
```bash
export GITHUB_TOKEN=github_pat_xxx
export GITHUB_REPO=linchen-20200325/2026_strategy_0719
```
webhook 收到「加 2330」→ 寫進 repo;cron `--per-user` → 讀同一份 → 逐人推。三邊自動共用。

## 三、怎麼拿到 `WATCH_OWNER_ID`（你的 userId）

userId 是 `U` 開頭 33 碼、**不是**你看得到的 LINE ID，只能經 bot 取得：
- 對你的 LINE bot 傳 **`id`** → 它回你的 userId（`nas_line_bot.py` 已支援），或
- 用你 mynews 那支 bot 傳 `id` 拿同一串（同一個 LINE 帳號 userId 因 channel 而異，**要用這個 OA 的**）。

## 四、安全 / 隱私

- `GITHUB_TOKEN` 只給**單一 repo、contents 權限**即可，別給全帳號權。
- `subscribers.json` 會含 userId，寫進 repo → **只適用私有 repo**。公開 repo 請改本機 backend（不設 GitHub 變數）。
- token 一律走 Secrets / env，**不要**進版控。
