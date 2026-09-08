"""路徑與設定。

金鑰沿用既有的 core/settings.py（.env 解析、Channel 對照、遮蔽顯示），
這裡只補上桌面應用需要的部分：使用者可寫入的資料夾、命中結果存放位置。

存檔一律放在使用者資料夾（%APPDATA%\\CrackPre），不寫進程式安裝目錄，
這樣即使之後打包成唯讀安裝也能運作。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import settings as backend_settings  # core/settings.py

APP_NAME = "CrackPre"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def hits_dir() -> Path:
    """命中結果（超過門檻的畫面）存放處。"""
    d = app_data_dir() / "hits"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource_path(rel: str) -> Path:
    """解析隨附資源；同時支援 PyInstaller、cx_Freeze 與直接跑原始碼。"""
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    elif getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = _REPO_ROOT
    return base / rel


def env_path() -> Path:
    return Path(backend_settings.ENV_PATH) if backend_settings.ENV_PATH else \
        _REPO_ROOT / ".env"


# --- 直接轉發到既有 settings.py，避免兩套設定各說各話 -----------------------

def line_token() -> str:
    return backend_settings.LINE_CHANNEL_ACCESS_TOKEN


def line_channels() -> list:
    return backend_settings.load_channels()


def imgbb_api_key() -> str:
    return backend_settings.IMGBB_API_KEY


def describe() -> str:
    return backend_settings.describe()


def missing_required() -> list:
    return backend_settings.missing_required()


def reload_env() -> None:
    """Settings 對話框存檔後重新載入，讓新值立刻生效。"""
    import importlib

    importlib.reload(backend_settings)
    for mod_name in ("uploader", "notifier"):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            importlib.reload(mod)


#: Settings 對話框會編輯的欄位
EDITABLE_KEYS = [
    ("LINE_CHANNEL_ACCESS_TOKEN", "LINE Channel access token", True),
    ("LINE_CHANNEL_SECRET", "LINE Channel secret", True),
    ("IMGBB_API_KEY", "ImgBB API key", True),
    ("LINE_CHANNEL_1", "LINE 推播目標 1", False),
    ("LINE_CHANNEL_2", "LINE 推播目標 2", False),
    ("LINE_CHANNEL_3", "LINE 推播目標 3", False),
]


def read_env_file() -> dict:
    """讀出 .env 目前的所有鍵值（含我們不編輯的）。"""
    path = env_path()
    if not path.exists():
        return {}
    return backend_settings._parse_env_file(str(path))


def save_env(values: dict) -> Path:
    """把 values 寫回 .env，保留檔案裡其他未列出的鍵，然後重新載入。

    刻意整份重寫而不是逐行 patch：這個 .env 是我們自己產生的格式，重寫比較
    不會因為引號或註解的邊界情況出錯。未知的鍵會原樣保留在檔尾。
    """
    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_env_file()
    existing.update({k: (v or "") for k, v in values.items()})

    known = [k for k, _, _ in EDITABLE_KEYS] + ["CRACK_BACKEND"]
    lines = ["# 由 CrackPre 設定對話框寫入。含金鑰，不要提交。", ""]
    for key in known:
        lines.append(f"{key}={existing.get(key, '')}")
    extras = [k for k in existing if k not in known]
    if extras:
        lines += ["", "# 其他設定"]
        lines += [f"{k}={existing[k]}" for k in sorted(extras)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for key, val in existing.items():
        os.environ[key] = val
    reload_env()
    return path
