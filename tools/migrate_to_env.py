"""一次性：把舊的 config.ini 設定搬進 .env。

    python migrate_to_env.py --channel-secret <LINE channel secret>

會讀 config.ini 的 CHANNEL_ACCESS_TOKEN 與 Channel-N，加上指令列給的
channel secret，寫成 .env。已存在的 .env 不會被覆蓋（除非加 --force），
而且原本 .env 裡已經填好的值會被保留。

imgbb 的 API key 不在舊設定裡，請自己到 https://api.imgbb.com/ 申請後填入。
"""

from __future__ import annotations

# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---

import argparse
import configparser
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
import paths

CONFIG = paths.LEGACY_CONFIG
ENV = paths.ENV_FILE

TEMPLATE = """\
# 由 migrate_to_env.py 從 config.ini 產生。這個檔案含有金鑰，不要提交。

# --- LINE ---------------------------------------------------------------
LINE_CHANNEL_ACCESS_TOKEN={token}
LINE_CHANNEL_SECRET={secret}

{channels}

# --- 圖片上傳 -----------------------------------------------------------
# Imgur API 已停用，改用 imgbb。到 https://api.imgbb.com/ 免費申請後填入。
IMGBB_API_KEY={imgbb}
IMGBB_EXPIRATION=
UPLOAD_TIMEOUT=30

# --- Email 通知（選用） -------------------------------------------------
GMAIL_APP_PASSWORD={gmail}
"""


def existing_env():
    """讀回目前 .env 已有的值，避免覆蓋掉使用者已經填好的東西。"""
    values = {}
    if not os.path.exists(ENV):
        return values
    with open(ENV, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip()
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel-secret", default="",
                        help="LINE channel secret（原本寫死在 LineBot.py 裡的那組）")
    parser.add_argument("--imgbb-key", default="", help="imgbb API key")
    parser.add_argument("--force", action="store_true", help="覆蓋已存在的 .env")
    args = parser.parse_args()

    prev = existing_env()
    if prev and not args.force:
        print(f"{ENV} 已存在，將保留其中已填好的值（要完全重寫請加 --force）")

    cp = configparser.ConfigParser()
    cp.read(CONFIG, encoding="utf-8")

    token = prev.get("LINE_CHANNEL_ACCESS_TOKEN") or cp.get(
        "line-Token", "CHANNEL_ACCESS_TOKEN", fallback="")
    secret = args.channel_secret or prev.get("LINE_CHANNEL_SECRET", "")
    imgbb = args.imgbb_key or prev.get("IMGBB_API_KEY", "")
    gmail = prev.get("GMAIL_APP_PASSWORD", "")

    lines = ["# 推播目標，索引從 1 起算，對應按鍵 1~9"]
    if cp.has_section("line-Channel"):
        for key in sorted(cp["line-Channel"]):
            if not key.startswith("channel-"):
                continue
            idx = key.split("-")[1]
            envkey = f"LINE_CHANNEL_{idx}"
            lines.append(f"{envkey}={prev.get(envkey) or cp['line-Channel'][key]}")
    for k, v in prev.items():
        if k.startswith("LINE_CHANNEL_") and k[len("LINE_CHANNEL_"):].isdigit():
            if not any(line.startswith(k + "=") for line in lines):
                lines.append(f"{k}={v}")

    if os.path.exists(ENV):
        shutil.copyfile(ENV, ENV + ".bak")
        print(f"已備份舊檔到 {ENV}.bak")

    with open(ENV, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(token=token, secret=secret,
                                channels="\n".join(lines), imgbb=imgbb, gmail=gmail))

    print(f"已寫出 {ENV}")
    import settings  # noqa: E402 - 寫完才 import，讀到的才是新檔
    print()
    print(settings.describe())
    miss = settings.missing_required()
    print("\n缺少必要設定: " + (", ".join(miss) if miss else "無"))
    if "IMGBB_API_KEY" in miss:
        print("  -> 到 https://api.imgbb.com/ 免費申請後填進 .env")


if __name__ == "__main__":
    main()
