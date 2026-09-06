"""精度回歸：拿 raw/ 底下的真實影像，比對舊路徑與新後端的輸出。

單例化不改變數值（同一個模型、同一組 cfg），所以 detectron2 後端應該要
**逐位元相同**。真正需要盯的是 ONNX / TorchScript 後端——tracing 之後
數值會有微小漂移，這支腳本就是用來確認漂移在可接受範圍內。

    python compare_legacy.py                      # 對照目前自動挑到的後端
    python compare_legacy.py --backend onnx
    python compare_legacy.py --backend detectron2 # 應為 0 差異
    python compare_legacy.py --limit 10 --save-diff

判定標準（可用 --tol-* 調整）：
    偵測數量        必須完全一致
    score           最大差 < 0.01
    box             最大差 < 2 px
    mask IoU        最小 > 0.95
任何一項不過就 exit 1。
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
import sys
import time

import cv2
import numpy as np

import crack_engine as ce
import paths


def collect_images(root=None, limit=None):
    root = root or paths.DATA_RAW
    """收集原始影像，排除先前產生的 *pre.jpg。"""
    found = []
    for dirpath, _, files in os.walk(root):
        for name in sorted(files):
            if not name.lower().endswith((".jpg", ".png")):
                continue
            stem = os.path.splitext(name)[0]
            if stem.endswith("pre"):
                continue
            found.append(os.path.join(dirpath, name))
    found.sort()
    return found[:limit] if limit else found


def build_legacy_predictor():
    """重現舊 CRACKpre.py 的 cfg。

    這裡只建一次——重建 predictor 影響的是速度不是數值，精度比對不需要
    每張都重來（那要跑好幾分鐘）。速度請看 bench_latency.py。
    """
    import torch
    from detectron2 import config, model_zoo
    from detectron2.engine import DefaultPredictor

    cfg = config.get_cfg()
    cfg.merge_from_file(
        model_zoo.get_config_file("COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml")
    )
    cfg.MODEL.ROI_HEADS.NUM_CLASSES = 3
    cfg.MODEL.WEIGHTS = paths.MODEL_WEIGHTS
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = 0.5
    cfg.MODEL.DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    return DefaultPredictor(cfg)


def legacy_infer(predictor, bgr):
    inst = predictor(bgr)["instances"].to("cpu")
    masks = (
        inst.pred_masks.numpy().astype(bool)
        if inst.has("pred_masks")
        else np.zeros((len(inst), bgr.shape[0], bgr.shape[1]), bool)
    )
    return (
        inst.pred_boxes.tensor.numpy().astype(np.float32),
        inst.scores.numpy().astype(np.float32),
        inst.pred_classes.numpy().astype(np.int64),
        masks,
    )


def mask_iou(a, b):
    if a.size == 0 or b.size == 0:
        return 1.0
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return 1.0 if union == 0 else float(inter) / float(union)


def box_iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return 0.0 if ua <= 0 else float(inter) / float(ua)


def greedy_match(lb, nb):
    """依 box IoU 貪婪配對，回傳 [(legacy_idx, new_idx), ...]。

    先前用 score 排名配對，只要兩筆分數接近就可能排錯位，把一個 5 px 的
    差異放大成 547 px 的假警報。改用 IoU 配對才反映真實差異。
    """
    pairs, used = [], set()
    for i in range(len(lb)):
        best, best_iou = -1, 0.0
        for j in range(len(nb)):
            if j in used:
                continue
            iou = box_iou(lb[i], nb[j])
            if iou > best_iou:
                best, best_iou = j, iou
        if best >= 0:
            pairs.append((i, best))
            used.add(best)
    return pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default=None,
                        help="onnx / torchscript / detectron2；預設用 CRACK_BACKEND 或 auto")
    parser.add_argument("--root", default=paths.DATA_RAW)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--tol-score", type=float, default=0.01)
    parser.add_argument("--tol-box", type=float, default=2.0)
    parser.add_argument("--min-iou", type=float, default=0.95)
    parser.add_argument("--save-diff", action="store_true",
                        help="把有差異的影像並排存到 raw/_diff/")
    args = parser.parse_args()

    if args.backend:
        os.environ["CRACK_BACKEND"] = args.backend
        ce.BACKEND = args.backend

    images = collect_images(args.root, args.limit)
    if not images:
        raise SystemExit(f"{args.root}/ 底下找不到影像")
    print(f"比對 {len(images)} 張影像\n")

    print("建立舊路徑 predictor（detectron2）…")
    try:
        legacy = build_legacy_predictor()
    except ImportError as exc:
        raise SystemExit(f"需要 detectron2 才能做精度回歸：{exc}")

    engine = ce.get_engine()
    print(f"新後端：{engine.backend.name}\n")

    worst = {"count": 0, "score": 0.0, "box": 0.0, "iou": 1.0}
    failures = []
    legacy_ms, new_ms = [], []

    for path in images:
        bgr = cv2.imread(path)
        if bgr is None:
            print(f"  跳過（讀不到）{path}")
            continue

        t = time.perf_counter()
        lb, ls, lc, lm = legacy_infer(legacy, bgr)
        legacy_ms.append((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        det = engine.infer(bgr)
        new_ms.append((time.perf_counter() - t) * 1000)

        problems = []
        if len(ls) != len(det.scores):
            problems.append(f"偵測數 {len(ls)} -> {len(det.scores)}")
            worst["count"] += 1
        else:
            n = len(ls)
            if n:
                pairs = greedy_match(lb, det.boxes)
                if len(pairs) < n:
                    problems.append(f"只配對到 {len(pairs)}/{n} 筆")
                ds = max((abs(float(ls[i] - det.scores[j])) for i, j in pairs), default=0.0)
                db = max((float(np.abs(lb[i] - det.boxes[j]).max()) for i, j in pairs), default=0.0)
                dc = sum(1 for i, j in pairs if lc[i] != det.classes[j])
                ious = [mask_iou(lm[i], det.masks[j]) for i, j in pairs] \
                    if lm.size and det.masks.size else [1.0]
                iou = float(min(ious)) if ious else 1.0

                worst["score"] = max(worst["score"], ds)
                worst["box"] = max(worst["box"], db)
                worst["iou"] = min(worst["iou"], iou)

                if ds > args.tol_score:
                    problems.append(f"score 差 {ds:.4f}")
                if db > args.tol_box:
                    problems.append(f"box 差 {db:.2f}px")
                if dc:
                    problems.append(f"class 不同 {dc} 筆")
                if iou < args.min_iou:
                    problems.append(f"mask IoU {iou:.3f}")

        tag = os.path.relpath(path, args.root)
        if problems:
            failures.append((tag, problems))
            print(f"  DIFF  {tag}: {'; '.join(problems)}")
            if args.save_diff:
                from visualize import draw_detections
                os.makedirs(os.path.join(args.root, "_diff"), exist_ok=True)
                ref = ce.Detections(boxes=lb, scores=ls, classes=lc,
                                    masks=lm if lm.size else np.zeros((0, 0, 0), bool))
                side = np.hstack([draw_detections(bgr, ref), draw_detections(bgr, det)])
                out = os.path.join(args.root, "_diff", tag.replace(os.sep, "_"))
                cv2.imwrite(out, side)
        else:
            print(f"  ok    {tag}  ({len(ls)} 處)")

    def p50(xs):
        return sorted(xs)[len(xs) // 2] if xs else 0.0

    print(f"\n{'=' * 60}")
    print(f"最大 score 差 : {worst['score']:.5f}   (容忍 {args.tol_score})")
    print(f"最大 box 差   : {worst['box']:.3f} px  (容忍 {args.tol_box})")
    print(f"最小 mask IoU : {worst['iou']:.4f}     (下限 {args.min_iou})")
    print(f"偵測數不符    : {worst['count']} 張")
    print(f"延遲 p50      : 舊 {p50(legacy_ms):.1f} ms -> 新 {p50(new_ms):.1f} ms")
    print(f"{'=' * 60}")

    if failures:
        print(f"\n{len(failures)}/{len(images)} 張超出容忍範圍：")
        for tag, problems in failures:
            print(f"  {tag}: {'; '.join(problems)}")
        print("\n判定：不通過。ONNX/TorchScript 後端不要上線，先用 detectron2 後端。")
        sys.exit(1)

    print("\n判定：通過。")


if __name__ == "__main__":
    main()
