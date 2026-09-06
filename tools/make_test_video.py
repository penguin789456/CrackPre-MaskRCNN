"""把 raw/ 底下的影像拼成一支測試影片，讓 RTSP.py 不需要無人機也能跑。

    python make_test_video.py
    set CRACK_RTSP_URL=raw/_test_source.mp4
    python RTSP.py            # 按 c 開連續判別

RTSP.py 偵測到來源是本機檔案就會循環播放，並照影片 fps 節流。
"""

from __future__ import annotations

# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---

import argparse
import os

import cv2

import paths

OUT_DEFAULT = os.path.join(paths.DATA_RAW, "_test_source.mp4")


def collect(root=None, limit=None):
    root = root or paths.DATA_RAW
    # 區域變數不叫 paths：那會蓋掉 paths 模組
    found = []
    for dirpath, _, files in os.walk(root):
        for name in sorted(files):
            if not name.lower().endswith((".jpg", ".png")):
                continue
            if os.path.splitext(name)[0].endswith("pre"):
                continue          # 跳過先前產生的標註圖
            found.append(os.path.join(dirpath, name))
    found.sort()
    return found[:limit] if limit else found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=OUT_DEFAULT)
    parser.add_argument("--fps", type=float, default=15.0)
    parser.add_argument("--hold", type=int, default=8,
                        help="每張圖重複幾格，讓畫面不會閃太快")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    args = parser.parse_args()

    paths = collect(limit=args.limit)
    if not paths:
        raise SystemExit(f"{paths.DATA_RAW} 底下找不到影像")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    size = (args.width, args.height)
    writer = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), args.fps, size)
    if not writer.isOpened():
        raise SystemExit(f"無法建立影片檔: {args.out}")

    written = 0
    for p in paths:
        img = cv2.imread(p)
        if img is None:
            continue
        if (img.shape[1], img.shape[0]) != size:
            img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
        for _ in range(args.hold):
            writer.write(img)
            written += 1
    writer.release()

    seconds = written / args.fps if args.fps else 0
    print(f"已寫出 {args.out}")
    print(f"  來源 {len(paths)} 張圖 -> {written} 格 / {seconds:.1f} 秒 @ {args.fps:.0f} fps")
    print(f"  大小 {os.path.getsize(args.out) / 1e6:.1f} MB")
    print()
    print("接著執行：")
    print(f"  set CRACK_RTSP_URL={args.out}")
    print("  python RTSP.py")


if __name__ == "__main__":
    main()
