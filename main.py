"""CrackPre 啟動器 —— 從專案根目錄執行：`python main.py`。

放在根目錄，所以 `crackpre_app` 不需要動 PYTHONPATH 就 import 得到。
推論後端在 core/ 底下（crack_engine、settings、uploader…），
那些是散裝模組而不是套件，所以這裡把該目錄加進 sys.path。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(_ROOT, "core")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crackpre_app.app import main  # noqa: E402  必須在 sys.path 設好之後

if __name__ == "__main__":
    raise SystemExit(main())
