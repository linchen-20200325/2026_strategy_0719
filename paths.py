"""paths.py — 檔案路徑單一真相源（SSOT）。L0，無 L1+ 相依。

「別亂放資料」：所有**執行期產生的資料**落地位置只在這裡定義一次，禁止各模組散寫
bare 檔名（那會依 CWD 落到 repo root，跑一次髒一次）。路徑一律以 repo 根為錨
（`Path(__file__)`），與 CWD 無關 → 從任何目錄跑，資料都進同一個分類好的位置。

分類
----
- `DATA_DIR`      本地執行期資料（forward-test ledger）。gitignore,不入主分支。
- `DEMO_DATA_DIR` `scripts/seed_demo_dbs.py` 產生的示範 DB（物理隔離,gitignore）。
- `LEDGER_FILE` / `STOCK_LEDGER_FILE`  大盤 / 個股判讀 ledger（append-only JSONL）。

覆寫順序（見 ledger.store / stock_store 的 `_path`）：
    顯式 path 參數  >  環境變數（LEDGER_FILE / STOCK_LEDGER_FILE）  >  本檔 SSOT 預設
CI（run_pipeline.yml）以 env 指定 bare 檔名並存回 ledger 分支（扁平 data 分支,刻意）；
本地不設 env → 落到 `DATA_DIR`。兩者互不干擾。

來源 DB（stock/fund/news.db）不在此：走 env `STOCK_DB` / `FUND_DB` / `NEWS_DB`
（見 `multi_agent_system/pipeline/watchlist.load_db_paths`）或 demo → `DEMO_DATA_DIR`。
"""

from __future__ import annotations

from pathlib import Path

# repo 根（本檔所在目錄）—— 所有相對路徑的錨點,與 CWD 無關。
REPO_ROOT: Path = Path(__file__).resolve().parent

# 本地執行期資料根（ledger 累積）。gitignore（`/data/`）,不入主分支。
DATA_DIR: Path = REPO_ROOT / "data"

# 示範 DB 目錄（seed_demo_dbs 產生,物理隔離）。gitignore（`demo_data/`）。
DEMO_DATA_DIR: Path = REPO_ROOT / "demo_data"

# forward-test ledger（append-only JSONL）——大盤判讀 / 個股判讀各一檔。
LEDGER_FILE: str = str(DATA_DIR / "ledger.jsonl")
STOCK_LEDGER_FILE: str = str(DATA_DIR / "stock_ledger.jsonl")
