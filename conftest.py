"""conftest.py — 確保 repo root 在 sys.path，讓 `import config` / `multi_agent_system` 可解析。"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
