"""集中管理設定與金鑰，來源是 .env。

原本金鑰散在三個地方，而且全是明文：
    config.ini          LINE CHANNEL_ACCESS_TOKEN、imgur CLIENT_ID、Channel ID
    LineBot.py:28       LINE channel secret 直接寫死在程式碼裡
    os.getenv           GMAIL_APP_PASSWORD（只有這個是對的）

現在統一從 .env 讀。.env 已加進 .gitignore，不會被帶進版控。

刻意不依賴 python-dotenv：這個專案的痛點就是裝套件麻煩，一個 30 行的
parser 能解決的事不值得再多一個依賴。若環境裡剛好有 python-dotenv 也不衝突。

查找順序（先找到的先用）：
    CrackPre/.env  ->  專案根目錄/.env  ->  作業系統環境變數
真正的環境變數優先於 .env，方便 CI 或臨時覆寫。
"""

from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)

#: .env 放在專案根目錄；HERE 這條是相容舊版把它放在模組旁邊的情況
ENV_CANDIDATES = [
    os.path.join(PROJECT_ROOT, ".env"),
    os.path.join(HERE, ".env"),
]

#: 已淘汰的 config.ini，只在 .env 缺項時當過渡 fallback
LEGACY_CONFIG = os.path.join(PROJECT_ROOT, "legacy", "config.ini")


def _parse_env_file(path):
    """極簡 .env parser：KEY=VALUE，支援引號、註解與 export 前綴。"""
    values = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            else:
                # 只在未加引號時才砍行內註解，否則會誤傷含 # 的金鑰
                hash_pos = val.find(" #")
                if hash_pos != -1:
                    val = val[:hash_pos].strip()
            values[key] = val
    return values


def _load():
    loaded = {}
    for path in ENV_CANDIDATES:
        if os.path.exists(path):
            loaded = _parse_env_file(path)
            globals()["ENV_PATH"] = path
            break
    else:
        globals()["ENV_PATH"] = None
    # 真實環境變數優先
    for key, val in loaded.items():
        os.environ.setdefault(key, val)
    return loaded


ENV_PATH = None
_FILE_VALUES = _load()

# --- 舊的 config.ini：只在 .env 缺該項時當作過渡用的 fallback -----------------
_legacy = None


def _legacy_get(section, option):
    global _legacy
    if _legacy is None:
        import configparser

        _legacy = configparser.ConfigParser()
        _legacy.read(LEGACY_CONFIG, encoding="utf-8")
    try:
        return _legacy.get(section, option)
    except Exception:
        return ""


def get(key, default="", legacy=None):
    """讀取設定。優先序：環境變數/.env  ->  config.ini（過渡）  ->  default。"""
    val = os.environ.get(key, "")
    if val:
        return val
    if legacy:
        val = _legacy_get(*legacy)
        if val:
            return val
    return default


# --- LINE --------------------------------------------------------------------
LINE_CHANNEL_ACCESS_TOKEN = get(
    "LINE_CHANNEL_ACCESS_TOKEN", legacy=("line-Token", "CHANNEL_ACCESS_TOKEN")
)
LINE_CHANNEL_SECRET = get("LINE_CHANNEL_SECRET")

# --- 圖片上傳（imgbb 取代已停用的 imgur） -------------------------------------
IMGBB_API_KEY = get("IMGBB_API_KEY")
IMGBB_UPLOAD_URL = get("IMGBB_UPLOAD_URL", "https://api.imgbb.com/1/upload")
# 0 或空 = 永不過期；否則為秒數（imgbb 接受 60~15552000）
IMGBB_EXPIRATION = get("IMGBB_EXPIRATION", "")
UPLOAD_TIMEOUT = float(get("UPLOAD_TIMEOUT", "30"))

# --- Email（選用） ------------------------------------------------------------
GMAIL_APP_PASSWORD = get("GMAIL_APP_PASSWORD")


def load_channels():
    """讀出 index -> LINE Channel ID 的對照表。

    .env 用 LINE_CHANNEL_1 / LINE_CHANNEL_2 …；
    若 .env 沒設，退回舊的 config.ini [line-Channel] Channel-N。
    索引 0 保留不用，維持既有「Channel-1 對應索引 1」的慣例。
    """
    channels = []

    def put(index, value):
        while len(channels) <= index:
            channels.append(None)
        channels[index] = value

    found = False
    for key, val in os.environ.items():
        if not key.startswith("LINE_CHANNEL_") or not val:
            continue
        suffix = key[len("LINE_CHANNEL_"):]
        if not suffix.isdigit():
            continue          # 跳過 LINE_CHANNEL_ACCESS_TOKEN / _SECRET
        put(int(suffix), val)
        found = True

    if not found:
        import configparser

        cp = configparser.ConfigParser()
        cp.read(LEGACY_CONFIG, encoding="utf-8")
        if cp.has_section("line-Channel"):
            for key in cp["line-Channel"]:
                if not key.startswith("channel-"):
                    continue
                try:
                    put(int(key.split("-")[1]), cp["line-Channel"][key])
                except (ValueError, IndexError):
                    print(f"[settings] 無法解析 Channel 索引: {key}")
    return channels


def _mask(value):
    if not value:
        return "(未設定)"
    return f"{value[:4]}…{value[-4:]} ({len(value)} 字元)"


def describe():
    """印出設定摘要，金鑰一律遮蔽。用來確認 .env 有被讀到。"""
    channels = load_channels()
    lines = [
        f".env 路徑                 : {ENV_PATH or '(找不到，改用環境變數/config.ini)'}",
        f"LINE_CHANNEL_ACCESS_TOKEN : {_mask(LINE_CHANNEL_ACCESS_TOKEN)}",
        f"LINE_CHANNEL_SECRET       : {_mask(LINE_CHANNEL_SECRET)}",
        f"IMGBB_API_KEY             : {_mask(IMGBB_API_KEY)}",
        f"Channel 數量              : {sum(1 for c in channels if c)}",
    ]
    for i, c in enumerate(channels):
        if c:
            lines.append(f"  Channel {i}               : {_mask(c)}")
    return "\n".join(lines)


def missing_required():
    """回傳缺少的必要設定名稱，供啟動時檢查。"""
    missing = []
    if not LINE_CHANNEL_ACCESS_TOKEN:
        missing.append("LINE_CHANNEL_ACCESS_TOKEN")
    if not IMGBB_API_KEY:
        missing.append("IMGBB_API_KEY")
    if not any(load_channels()):
        missing.append("LINE_CHANNEL_1")
    return missing


if __name__ == "__main__":
    print(describe())
    miss = missing_required()
    print("\n缺少必要設定: " + (", ".join(miss) if miss else "無"))
