"""一次性：把 output/model_final.pth 匯出成 TorchScript / ONNX。

這一步是「環境瘦身」的關鍵。匯出只需要在原本那個重量級環境裡跑一次，
之後部署端就不必再安裝 detectron2、pycocotools、fvcore 與 C++ Build Tools。

    (CrackPre) > python export_slim_model.py --format both

產物放在 output/：

    model.ts               TorchScript（需要 torch，不需要 detectron2）
    model.onnx             ONNX（只需要 onnxruntime）
    model_manifest.json    輸出張量的順序，crack_engine 會讀

驗證：匯出後腳本會用同一張圖分別跑 detectron2 與匯出模型，比對 box / score，
差異過大會直接報錯，不要拿沒驗證過的模型上線。
"""

from __future__ import annotations

# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---

import argparse
import json
import os

import cv2
import numpy as np
import torch

import paths

from detectron2 import config, model_zoo
from detectron2.checkpoint import DetectionCheckpointer
from detectron2.export import TracingAdapter
from detectron2.modeling import build_model

from crack_engine import (
    CLASS_NAMES,
    MAX_SIZE_TEST,
    MIN_SIZE_TEST,
    OUTPUT_DIR,
    SCORE_THRESH,
    resize_shortest_edge,
    to_chw_float,
)

WEIGHTS = os.path.join(OUTPUT_DIR, "model_final.pth")
ONNX_OPSET = 16


def build_cfg():
    cfg = config.get_cfg()
    cfg.merge_from_file(
        model_zoo.get_config_file("COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml")
    )
    cfg.MODEL.ROI_HEADS.NUM_CLASSES = len(CLASS_NAMES)
    cfg.MODEL.WEIGHTS = WEIGHTS
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = SCORE_THRESH
    cfg.INPUT.MIN_SIZE_TEST = MIN_SIZE_TEST
    cfg.INPUT.MAX_SIZE_TEST = MAX_SIZE_TEST
    # 一律用 CPU 匯出：traced graph 才不會把 cuda device 寫死進去。
    cfg.MODEL.DEVICE = "cpu"
    cfg.freeze()
    return cfg


def sample_input(image_path=None):
    """準備一張代表性的輸入。有真圖就用真圖，tracing 的分支才貼近實際。"""
    if image_path and os.path.exists(image_path):
        bgr = cv2.imread(image_path)
    else:
        bgr = None
    if bgr is None:
        for root, _, files in os.walk(paths.DATA_RAW):
            for name in files:
                if name.lower().endswith((".jpg", ".png")):
                    bgr = cv2.imread(os.path.join(root, name))
                    if bgr is not None:
                        print(f"[export] 使用樣本影像 {os.path.join(root, name)}")
                        break
            if bgr is not None:
                break
    if bgr is None:
        print("[export] 找不到樣本影像，改用隨機雜訊（建議放一張真實裂縫圖到 raw/）")
        bgr = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
    resized, h, w = resize_shortest_edge(bgr)
    return bgr, torch.from_numpy(to_chw_float(resized)), (h, w)


def output_names(model, image_tensor):
    """TracingAdapter 攤平 Instances 時是把欄位名排序後接 image_size。

    這裡直接跑一次真的 inference 拿欄位名，避免硬編順序。
    """
    with torch.no_grad():
        inst = model.inference([{"image": image_tensor}], do_postprocess=False)[0]
    names = sorted(inst.get_fields().keys()) + ["image_size"]
    shapes = {k: tuple(v.tensor.shape) if hasattr(v, "tensor") else tuple(v.shape)
              for k, v in inst.get_fields().items()}
    return names, shapes, inst


def make_adapter(model, image_tensor):
    def inference(m, inputs):
        inst = m.inference(inputs, do_postprocess=False)[0]
        return [{"instances": inst}]

    adapter = TracingAdapter(model, [{"image": image_tensor}], inference)
    adapter.eval()
    return adapter


def export_torchscript(adapter, image_tensor, path):
    with torch.no_grad():
        ts = torch.jit.trace(adapter, (image_tensor,), strict=False)
    torch.jit.save(ts, path)
    print(f"[export] 已寫出 {path} ({os.path.getsize(path) / 1e6:.0f} MB)")
    return torch.jit.load(path)


def export_onnx(adapter, image_tensor, path, names):
    with torch.no_grad():
        torch.onnx.export(
            adapter,
            (image_tensor,),
            path,
            opset_version=ONNX_OPSET,
            input_names=["image"],
            output_names=names,
            # 影像尺寸會隨來源改變，H/W 必須是 dynamic。
            dynamic_axes={"image": {1: "height", 2: "width"}},
            do_constant_folding=True,
        )
    print(f"[export] 已寫出 {path} ({os.path.getsize(path) / 1e6:.0f} MB)")


def compare(reference, candidate, label):
    """比對 box 與 score，容忍小幅數值誤差。"""
    ref_boxes, ref_scores = reference
    cand_boxes, cand_scores = candidate
    if ref_boxes.shape[0] != cand_boxes.shape[0]:
        raise SystemExit(
            f"[export] {label} 驗證失敗：偵測數量不同 "
            f"(detectron2={ref_boxes.shape[0]}, {label}={cand_boxes.shape[0]})"
        )
    if ref_boxes.shape[0] == 0:
        print(f"[export] {label} 驗證：兩邊都是 0 個偵測（樣本圖沒有裂縫，僅代表流程可跑）")
        return
    box_err = float(np.abs(ref_boxes - cand_boxes).max())
    score_err = float(np.abs(ref_scores - cand_scores).max())
    print(f"[export] {label} 驗證：box 最大誤差 {box_err:.4f} px、score 最大誤差 {score_err:.5f}")
    if box_err > 1.0 or score_err > 1e-3:
        raise SystemExit(f"[export] {label} 驗證失敗：誤差超出容忍範圍")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["torchscript", "onnx", "both"], default="both")
    parser.add_argument("--sample", default=None, help="用來 tracing 的樣本圖片路徑")
    args = parser.parse_args()

    if not os.path.exists(WEIGHTS):
        raise SystemExit(f"找不到權重檔 {WEIGHTS}")

    cfg = build_cfg()
    model = build_model(cfg)
    model.eval()
    DetectionCheckpointer(model).load(cfg.MODEL.WEIGHTS)

    bgr, image_tensor, resized_hw = sample_input(args.sample)
    names, shapes, inst = output_names(model, image_tensor)
    print(f"[export] 輸出順序: {names}")
    print(f"[export] 欄位形狀: {shapes}")

    ref = (
        inst.pred_boxes.tensor.numpy().astype(np.float32),
        inst.scores.numpy().astype(np.float32),
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, "model_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "outputs": names,
                "class_names": CLASS_NAMES,
                "min_size_test": MIN_SIZE_TEST,
                "max_size_test": MAX_SIZE_TEST,
                "note": "boxes/masks 為 resize 後座標系，mask 為 ROI 尺寸；還原由 crack_engine 負責",
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print("[export] 已寫出 model_manifest.json")

    adapter = make_adapter(model, image_tensor)
    box_idx, score_idx = names.index("pred_boxes"), names.index("scores")

    if args.format in ("torchscript", "both"):
        ts_path = os.path.join(OUTPUT_DIR, "model.ts")
        loaded = export_torchscript(adapter, image_tensor, ts_path)
        with torch.no_grad():
            out = loaded(image_tensor)
        out = [t.detach().cpu().numpy() for t in out]
        compare(ref, (out[box_idx], out[score_idx]), "torchscript")

    if args.format in ("onnx", "both"):
        onnx_path = os.path.join(OUTPUT_DIR, "model.onnx")
        export_onnx(adapter, image_tensor, onnx_path, names)
        try:
            import onnxruntime as ort

            sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
            out = sess.run(None, {sess.get_inputs()[0].name: image_tensor.numpy()})
            compare(ref, (out[box_idx], out[score_idx]), "onnx")
        except ImportError:
            print("[export] 未安裝 onnxruntime，略過 ONNX 驗證（強烈建議裝起來驗一次）")

    print("[export] 完成。部署端只要帶走 output/ 目錄即可。")


if __name__ == "__main__":
    main()
