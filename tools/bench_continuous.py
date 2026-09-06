"""量測「連續影像判別」的行為，不需要無人機或 RTSP 來源。

`bench_latency.py` 量的是單張圖的推論時間。連續模式真正的問題不是單張多久，
而是**延遲會不會越積越多**——舊版每按一次就開一條執行緒，前一張還沒好、
下一張就排進去，你看到的結果離現場畫面越來越遠。

所以這裡的關鍵指標是「幀齡」(frame age)：從影格被送出、到 worker 真的開始
處理它，中間過了多久。

    幀齡持平  = 正確。舊幀被丟掉，永遠處理最新畫面。
    幀齡遞增  = 排隊了。延遲會無限累積，畫面與現實脫節。

用法：

    python bench_continuous.py --fake              # 秒級，不載模型，只驗管線
    python bench_continuous.py --seconds 60        # 用真模型
    python bench_continuous.py --compare           # 並排比較 latest vs 排隊
    python bench_continuous.py --source data/raw/TELLO  # 用資料夾當影像來源
"""

from __future__ import annotations

# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---

import argparse
import glob
import os
import queue
import statistics
import threading
import time

import cv2
import paths
import numpy as np

from pipeline import LatestFrameWorker


# --------------------------------------------------------------------------
# 影像來源
# --------------------------------------------------------------------------


def load_frames(source, limit=30):
    """讀進一小疊影格，循環播放當作串流。"""
    # 區域變數不叫 paths：那會蓋掉 paths 模組
    found = []
    if source and os.path.isdir(source):
        found = [p for p in sorted(glob.glob(os.path.join(source, "*.jpg")))
                 if not os.path.splitext(os.path.basename(p))[0].endswith("pre")]
    elif source and os.path.isfile(source):
        found = [source]
    else:
        for root, _, files in os.walk(paths.DATA_RAW):
            for name in sorted(files):
                if name.lower().endswith(".jpg") and not \
                        os.path.splitext(name)[0].endswith("pre"):
                    found.append(os.path.join(root, name))

    frames = []
    for p in found[:limit]:
        img = cv2.imread(p)
        if img is not None:
            frames.append(img)
    if not frames:
        print("找不到影像，改用 720p 隨機雜訊")
        frames = [np.random.randint(0, 255, (720, 1280, 3), np.uint8) for _ in range(3)]
    print(f"來源影格 {len(frames)} 張，解析度 {frames[0].shape[1]}x{frames[0].shape[0]}")
    return frames


# --------------------------------------------------------------------------
# 推論處理器
# --------------------------------------------------------------------------


def make_handler(stats, fake_ms=None):
    """回傳 handler(frame, meta)。meta 帶著送出時間，用來算幀齡。"""
    engine = None
    if fake_ms is None:
        from crack_engine import get_engine
        engine = get_engine()

    def handler(frame, meta):
        submitted_at, index = meta
        started = time.perf_counter()
        stats["age_ms"].append((started - submitted_at) * 1000.0)

        if engine is None:
            time.sleep(fake_ms / 1000.0)
            n = 0
        else:
            n = len(engine.infer(frame))

        done = time.perf_counter()
        stats["infer_ms"].append((done - started) * 1000.0)
        stats["e2e_ms"].append((done - submitted_at) * 1000.0)
        stats["detections"].append(n)

    return handler


class QueueingWorker:
    """重現舊行為：所有影格都排隊，一張都不丟。用來對照。"""

    def __init__(self, handler):
        self._handler = handler
        self._q = queue.Queue()
        self.processed = 0
        self.dropped = 0
        self._stop = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    @property
    def busy(self):
        return not self._q.empty()

    def submit(self, frame, meta=None):
        self._q.put((frame.copy(), meta))
        return False

    def _run(self):
        while not self._stop:
            try:
                frame, meta = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._handler(frame, meta)
                self.processed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[bench] 處理失敗: {exc}")

    def stop(self, timeout=5):
        self._stop = True
        self._t.join(timeout)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(len(xs) * p) - 1))]


def run(mode, frames, seconds, fps, fake_ms):
    stats = {"age_ms": [], "infer_ms": [], "e2e_ms": [], "detections": []}
    handler = make_handler(stats, fake_ms)

    if mode == "latest":
        worker = LatestFrameWorker(handler, min_interval=0.0, name="bench-latest")
    else:
        worker = QueueingWorker(handler)

    interval = 1.0 / fps
    submitted = 0
    t_end = time.perf_counter() + seconds
    next_at = time.perf_counter()

    while time.perf_counter() < t_end:
        now = time.perf_counter()
        if now < next_at:
            time.sleep(min(next_at - now, 0.01))
            continue
        frame = frames[submitted % len(frames)]
        worker.submit(frame, (time.perf_counter(), submitted))
        submitted += 1
        next_at += interval

    # 收尾：latest 模式頂多還有一幀在跑；排隊模式可能還有一大串
    deadline = time.perf_counter() + 30
    while worker.busy and time.perf_counter() < deadline:
        time.sleep(0.1)
    worker.stop()

    return {
        "mode": mode,
        "submitted": submitted,
        "processed": worker.processed,
        "dropped": getattr(worker, "dropped", 0),
        "backlog": submitted - worker.processed - getattr(worker, "dropped", 0),
        "stats": stats,
    }


def report(result, seconds):
    s = result["stats"]
    ages = s["age_ms"]
    name = "最新幀優先 (LatestFrameWorker)" if result["mode"] == "latest" \
        else "全部排隊 (舊行為)"
    print(f"\n--- {name} ---")
    print(f"  送入 {result['submitted']}　處理 {result['processed']}　"
          f"丟棄 {result['dropped']}　收尾時仍積壓 {result['backlog']}")
    print(f"  處理速率        : {result['processed'] / seconds:.2f} fps")
    print(f"  推論延遲        : p50 {pct(s['infer_ms'], .5):8.1f} ms   "
          f"p95 {pct(s['infer_ms'], .95):8.1f} ms")
    print(f"  幀齡 (關鍵指標) : p50 {pct(ages, .5):8.1f} ms   "
          f"p95 {pct(ages, .95):8.1f} ms   max {max(ages) if ages else 0:8.1f} ms")
    print(f"  端到端延遲      : p50 {pct(s['e2e_ms'], .5):8.1f} ms   "
          f"p95 {pct(s['e2e_ms'], .95):8.1f} ms")

    if not ages:
        return

    # 判定用「幀齡中位數 / 推論時間中位數」的比值，而不是前後半段的平均差。
    # 理由：真的在排隊時，每幀都得等前面所有幀算完，幀齡必然是推論時間的數倍；
    # 沒排隊時 worker 幾乎立刻接手，幀齡遠小於推論時間。
    # 用平均差在樣本少的時候會被單一離群值帶偏（實測 7 個樣本就誤判過一次），
    # 而這個比值不受樣本數與離群值影響。
    age_p50 = pct(ages, .5)
    infer_p50 = pct(s["infer_ms"], .5) or 1.0
    ratio = age_p50 / infer_p50
    print(f"  幀齡 / 推論時間 : {ratio:.2f}x"
          f"　(<1 表示 worker 幾乎即時接手；>1 表示正在排隊)")

    if len(ages) >= 6:
        half = len(ages) // 2
        first = statistics.median(ages[:half])
        second = statistics.median(ages[half:])
        print(f"  幀齡趨勢(中位數): 前半 {first:.1f} ms -> 後半 {second:.1f} ms")

    if ratio > 1.5:
        print("  >>> 延遲正在累積：處理的是好幾幀之前的畫面，越跑落後越多")
    else:
        print("  >>> 幀齡持平：永遠處理最新畫面，延遲不累積")

    if len(ages) < 8:
        print(f"  （註：只有 {len(ages)} 個樣本，趨勢僅供參考；"
              f"比值判定不受樣本數影響）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=20, help="送圖持續秒數")
    parser.add_argument("--fps", type=float, default=15, help="模擬的來源幀率")
    parser.add_argument("--source", default=None, help="影像資料夾或單一檔案")
    parser.add_argument("--fake", action="store_true",
                        help="不載模型，用假的推論耗時（快速驗證管線機制）")
    parser.add_argument("--fake-ms", type=float, default=300,
                        help="--fake 時每張假裝花多少毫秒")
    parser.add_argument("--compare", action="store_true",
                        help="並排比較最新幀優先 vs 全部排隊")
    args = parser.parse_args()

    frames = load_frames(args.source)
    fake_ms = args.fake_ms if args.fake else None
    if fake_ms is not None:
        print(f"假推論模式：每張 {fake_ms:.0f} ms，不載入模型")

    print(f"\n送圖 {args.seconds:.0f} 秒 @ {args.fps:.0f} fps"
          f"（預計送入約 {int(args.seconds * args.fps)} 幀）")

    report(run("latest", frames, args.seconds, args.fps, fake_ms), args.seconds)
    if args.compare:
        report(run("queue", frames, args.seconds, args.fps, fake_ms), args.seconds)
        print("\n怎麼看這兩組數字：")
        print("  排隊模式「處理張數」比較多，但那不是優點——它只是把每一幀都算完，")
        print("  代價是每個結果都對應到好幾秒前的畫面，而且送圖停止後還要花很久才追得完。")
        print("  最新幀優先會丟掉來不及處理的舊幀，換到幀齡持平：看到的永遠是當下。")
        print("  連續巡檢要的是後者——你要知道『現在』牆上有沒有裂縫，不是三十秒前。")


if __name__ == "__main__":
    main()
