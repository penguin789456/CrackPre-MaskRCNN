"""用 OpenCV 畫偵測結果，取代 detectron2.utils.visualizer.Visualizer。

原本的 Visualizer 會拉進 matplotlib（含 Agg backend、字型快取、pycocotools），
不只讓環境變大，每張圖還要花 100~300 ms 在 figure 建立與 mask 輪廓計算上。
這裡只用 cv2，畫一張 1080p 大約 2~5 ms。
"""

from __future__ import annotations

import cv2
import numpy as np

from crack_engine import CLASS_NAMES

# BGR，三個裂縫類別各一色
COLORS = [
    (66, 135, 245),   # diagonal  - 橘藍
    (80, 220, 100),   # horizontal- 綠
    (60, 76, 231),    # vertical  - 紅
]

MASK_ALPHA = 0.45
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _color(class_id):
    return COLORS[int(class_id) % len(COLORS)]


def _label(class_id, score):
    idx = int(class_id)
    name = CLASS_NAMES[idx] if 0 <= idx < len(CLASS_NAMES) else f"class_{idx}"
    return f"{name} {score * 100:.0f}%"


def draw_detections(bgr, det, draw_masks=True):
    """回傳畫好標註的新影像（不修改輸入）。"""
    out = bgr.copy()
    n = len(det)
    if n == 0:
        return out

    if draw_masks and det.masks.size:
        # 一次疊完所有 mask，避免每個 instance 都做一次 full-frame blend。
        overlay = np.zeros_like(out)
        any_mask = np.zeros(out.shape[:2], bool)
        for i in range(n):
            m = det.masks[i]
            if not m.any():
                continue
            overlay[m] = _color(det.classes[i])
            any_mask |= m
        if any_mask.any():
            out[any_mask] = cv2.addWeighted(
                out, 1 - MASK_ALPHA, overlay, MASK_ALPHA, 0
            )[any_mask]

    for i in range(n):
        x0, y0, x1, y1 = det.boxes[i].astype(int)
        color = _color(det.classes[i])
        cv2.rectangle(out, (x0, y0), (x1, y1), color, 2)

        text = _label(det.classes[i], det.scores[i])
        (tw, th), base = cv2.getTextSize(text, FONT, 0.5, 1)
        ty = max(y0, th + base + 2)
        cv2.rectangle(out, (x0, ty - th - base - 2), (x0 + tw + 4, ty), color, -1)
        cv2.putText(out, text, (x0 + 2, ty - base), FONT, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

    return out
