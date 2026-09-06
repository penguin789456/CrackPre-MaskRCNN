"""量測連續判別的延遲，用來驗證改動是否真的有效。

    python bench_latency.py                 # 用目前的後端跑 20 次
    python bench_latency.py --n 50
    CRACK_BACKEND=detectron2 python bench_latency.py
    python bench_latency.py --legacy        # 額外跑一次「每張圖重建 predictor」的舊行為

輸出的 p50/p95 才是連續判別時真正感受到的延遲；平均值會被第一次的
模型載入洗掉，不要只看平均。
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
import statistics
import time

import cv2
import paths
import numpy as np

from crack_engine import get_engine


def find_sample():
    for root, _, files in os.walk(paths.DATA_RAW):
        for name in files:
            if name.lower().endswith((".jpg", ".png")) and "pre" not in name.lower():
                img = cv2.imread(os.path.join(root, name))
                if img is not None:
                    print(f"樣本影像: {os.path.join(root, name)}  {img.shape}")
                    return img
    print("data/raw 下找不到影像，改用 1280x720 隨機雜訊")
    return np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)


def report(label, samples):
    samples = sorted(samples)
    p50 = statistics.median(samples)
    p95 = samples[max(0, int(len(samples) * 0.95) - 1)]
    print(
        f"{label:>28}:  p50 {p50:7.1f} ms   p95 {p95:7.1f} ms   "
        f"min {samples[0]:7.1f} ms   max {samples[-1]:7.1f} ms   "
        f"≈ {1000.0 / p50:.2f} fps"
    )


def bench_engine(image, n):
    t0 = time.perf_counter()
    engine = get_engine()
    print(f"引擎（{engine.backend.name}）載入 + warmup 共 {time.perf_counter() - t0:.2f}s\n")

    samples = []
    for i in range(n):
        t = time.perf_counter()
        det = engine.infer(image)
        samples.append((time.perf_counter() - t) * 1000.0)
        if i == 0:
            print(f"第一次推論偵測到 {len(det)} 處")
    report(f"單例 / {engine.backend.name}", samples)


def bench_legacy(image, n):
    """重現舊版：每張圖都 DefaultPredictor(cfg)。"""
    try:
        import torch
        from detectron2 import config, model_zoo
        from detectron2.engine import DefaultPredictor
    except ImportError:
        print("\n未安裝 detectron2，略過 legacy 比較。")
        return

    from crack_engine import CLASS_NAMES, D2_WEIGHTS, SCORE_THRESH

    samples = []
    for _ in range(n):
        t = time.perf_counter()
        cfg = config.get_cfg()
        cfg.merge_from_file(
            model_zoo.get_config_file("COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml")
        )
        cfg.MODEL.ROI_HEADS.NUM_CLASSES = len(CLASS_NAMES)
        cfg.MODEL.WEIGHTS = D2_WEIGHTS
        cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = SCORE_THRESH
        cfg.MODEL.DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
        predictor = DefaultPredictor(cfg)
        predictor(image)
        samples.append((time.perf_counter() - t) * 1000.0)
    report("舊版 / 每張重建 predictor", samples)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--legacy", action="store_true", help="同時跑舊版行為做對照")
    args = parser.parse_args()

    image = find_sample()
    bench_engine(image, args.n)
    if args.legacy:
        bench_legacy(image, max(3, args.n // 4))


if __name__ == "__main__":
    main()
