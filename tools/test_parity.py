"""逐函式比對：crack_engine 的 numpy 實作 vs detectron2 的 torch 原版。

compare_legacy.py 是端到端的黑箱比對，跑一輪要十分鐘，而且只告訴你「有差」，
不會指出是哪一段。這支腳本直接把兩邊的對應函式擺在一起比，秒級就能定位。

需要 detectron2（只有匯出端要跑）。

    python test_parity.py
"""

from __future__ import annotations

# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---

import sys

import numpy as np
import torch

import crack_engine as ce

failures = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail if not ok else ''}".rstrip())
    if not ok:
        failures.append(name)


def test_resize_parity():
    """對照 detectron2.data.transforms.ResizeShortestEdge。"""
    print("resize_shortest_edge vs detectron2 ResizeShortestEdge")
    import detectron2.data.transforms as T

    rng = np.random.RandomState(0)
    for (h, w) in [(720, 1280), (1080, 1920), (240, 320), (900, 900), (1600, 900), (473, 631)]:
        img = rng.randint(0, 255, (h, w, 3), dtype=np.uint8)

        aug = T.ResizeShortestEdge([ce.MIN_SIZE_TEST, ce.MIN_SIZE_TEST],
                                   ce.MAX_SIZE_TEST, "choice")
        ref = aug.get_transform(img).apply_image(img)

        mine, nh, nw = ce.resize_shortest_edge(img)

        same_shape = mine.shape == ref.shape == (nh, nw, 3)
        maxdiff = int(np.abs(mine.astype(np.int32) - ref.astype(np.int32)).max()) \
            if same_shape else -1
        check(f"{h}x{w} -> {nh}x{nw}", same_shape and maxdiff == 0,
              f"shape ref={ref.shape} mine={mine.shape} maxdiff={maxdiff}")


def test_paste_parity():
    """對照 detectron2.layers.mask_ops.paste_masks_in_image。"""
    print("paste_roi_masks vs detectron2 paste_masks_in_image")
    from detectron2.layers.mask_ops import paste_masks_in_image

    rng = np.random.RandomState(1)
    cases = [
        # (影像高, 寬, box 陣列)
        (200, 300, [[10.0, 20.0, 60.0, 100.0]]),
        (200, 300, [[0.0, 0.0, 299.0, 199.0]]),
        (480, 640, [[12.3, 45.6, 300.9, 210.4], [400.0, 100.0, 639.0, 479.0]]),
        # 細長的裂縫形狀，最容易吃到對齊誤差
        (480, 640, [[100.5, 200.25, 520.75, 214.5]]),
        # 貼齊邊界
        (100, 100, [[0.0, 0.0, 3.5, 99.5]]),
    ]
    for h, w, boxes in cases:
        boxes = np.array(boxes, np.float32)
        n = len(boxes)
        # 用平滑的隨機 mask，比全 0/1 更容易暴露插值差異
        roi = rng.rand(n, 1, 28, 28).astype(np.float32)

        ref = paste_masks_in_image(
            torch.from_numpy(roi[:, 0]), torch.from_numpy(boxes), (h, w), threshold=0.5
        ).numpy().astype(bool)

        mine = ce.paste_roi_masks(roi, boxes, h, w)

        if mine.shape != ref.shape:
            check(f"{h}x{w} n={n}", False, f"shape ref={ref.shape} mine={mine.shape}")
            continue
        diff = int(np.logical_xor(mine, ref).sum())
        total = int(ref.sum()) or 1
        iou = float(np.logical_and(mine, ref).sum()) / max(
            1, int(np.logical_or(mine, ref).sum()))
        check(f"{h}x{w} n={n}", diff == 0,
              f"{diff} 個像素不同 (ref 前景 {total}, IoU {iou:.4f})")


def test_rescale_parity():
    """對照 detector_postprocess 的 scale -> clip -> nonempty。"""
    print("rescale + nonempty vs detectron2 detector_postprocess")
    from detectron2.structures import Boxes

    boxes = np.array([
        [10.0, 20.0, 110.0, 220.0],
        [-5.0, -5.0, 500.0, 500.0],
        [50.0, 50.0, 50.0, 80.0],     # 寬為 0，應被 nonempty 濾掉
    ], np.float32)
    from_hw, to_hw = (400, 400), (800, 600)

    b = Boxes(torch.from_numpy(boxes.copy()))
    b.scale(to_hw[1] / from_hw[1], to_hw[0] / from_hw[0])
    b.clip(to_hw)
    keep_ref = b.nonempty().numpy()
    ref = b.tensor.numpy()[keep_ref]

    mine = ce.rescale_boxes(boxes, from_hw, to_hw)
    keep = (mine[:, 2] - mine[:, 0] > 0) & (mine[:, 3] - mine[:, 1] > 0)
    mine = mine[keep]

    check("nonempty 濾除結果一致", bool((keep == keep_ref).all()),
          f"ref={keep_ref} mine={keep}")
    check("box 數值一致", mine.shape == ref.shape and np.allclose(mine, ref, atol=1e-4),
          f"ref={ref}\n      mine={mine}")


def main():
    for fn in (test_resize_parity, test_paste_parity, test_rescale_parity):
        fn()
        print()
    if failures:
        print(f"{len(failures)} 項不一致: {failures}")
        sys.exit(1)
    print("與 detectron2 完全一致。")


if __name__ == "__main__":
    main()
