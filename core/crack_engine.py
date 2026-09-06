"""裂縫偵測推論引擎。

這個模組解決原本 CRACKpre.PreImg() 的兩個問題：

1. 延遲：原本每呼叫一次 PreImg 就重新 build 一次 Mask R-CNN 並從磁碟載入
   176 MB 的 model_final.pth，每張圖都要付 3~8 秒的模型建構成本。
   這裡改成 process 內單例 + 執行緒安全的 lazy init + warmup。

2. 依賴：推論不再一定要 detectron2。引擎有三個後端，依序自動挑選：

       onnx        -> onnxruntime            (最輕，不需要 torch / detectron2 / C++ 編譯器)
       torchscript -> torch                  (中等，需要 torch 但不需要 detectron2 / 編譯器)
       detectron2  -> torch + detectron2     (原本的路徑，當作 fallback 與匯出來源)

   前處理（ResizeShortestEdge）與後處理（box 還原、28x28 ROI mask 貼回原圖）
   在這裡用純 numpy / OpenCV 重寫，所以 onnx / torchscript 後端完全不 import
   detectron2。

用法::

    from crack_engine import get_engine
    det = get_engine().infer(bgr_image)   # -> Detections
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

# --------------------------------------------------------------------------
# 設定
# --------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
#: 模型權重與匯出產物集中在專案根目錄的 models/
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "models")

CLASS_NAMES = ["diagonal_crack", "horizontal_crack", "vertical_crack"]

# 對應 Base-RCNN-FPN.yaml 的 INPUT.MIN_SIZE_TEST / MAX_SIZE_TEST。
# 兩者都調小可以近乎線性地換到更低的延遲，代價是小裂縫的召回率。
MIN_SIZE_TEST = int(os.getenv("CRACK_MIN_SIZE", "800"))
MAX_SIZE_TEST = int(os.getenv("CRACK_MAX_SIZE", "1333"))

SCORE_THRESH = float(os.getenv("CRACK_SCORE_THRESH", "0.5"))
MASK_THRESH = 0.5

# onnx / torchscript / detectron2 / auto
BACKEND = os.getenv("CRACK_BACKEND", "auto").lower()

ONNX_PATH = os.path.join(OUTPUT_DIR, "model.onnx")
TS_PATH = os.path.join(OUTPUT_DIR, "model.ts")
MANIFEST_PATH = os.path.join(OUTPUT_DIR, "model_manifest.json")
D2_WEIGHTS = os.path.join(OUTPUT_DIR, "model_final.pth")

VERBOSE = os.getenv("CRACK_VERBOSE", "1") != "0"


def _log(msg):
    if VERBOSE:
        print(f"[crack_engine] {msg}", flush=True)


# --------------------------------------------------------------------------
# 結果容器
# --------------------------------------------------------------------------


@dataclass
class Detections:
    """一張圖的偵測結果，座標皆為原圖座標系。"""

    boxes: np.ndarray = field(default_factory=lambda: np.zeros((0, 4), np.float32))
    scores: np.ndarray = field(default_factory=lambda: np.zeros((0,), np.float32))
    classes: np.ndarray = field(default_factory=lambda: np.zeros((0,), np.int64))
    masks: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 0), bool))
    latency_ms: float = 0.0

    def __len__(self):
        return int(self.scores.shape[0])

    @property
    def best_score(self):
        return float(self.scores.max()) if len(self) else 0.0


# --------------------------------------------------------------------------
# 前處理 / 後處理（純 numpy，detectron2-free）
# --------------------------------------------------------------------------


def resize_shortest_edge(bgr, min_size=MIN_SIZE_TEST, max_size=MAX_SIZE_TEST):
    """複製 detectron2.data.transforms.ResizeShortestEdge 在 test 時的行為。

    回傳 (resized_bgr, new_h, new_w)。

    重點：這裡**必須**用 PIL 的 BILINEAR，不能用 cv2.INTER_LINEAR。
    detectron2 的 ResizeTransform.apply_image 對 uint8 走的是
    ``Image.fromarray(img).resize(..., Image.BILINEAR)``，PIL 的 resize 是
    帶 support scaling 的重採樣濾波，跟 OpenCV 的 INTER_LINEAR 不同。
    先前用 cv2 導致送進網路的張量就跟 detectron2 不一樣，score 會漂
    0.01~0.05，連帶讓偵測數與類別都對不上（compare_legacy.py 抓到的）。
    """
    h, w = bgr.shape[:2]
    scale = min_size * 1.0 / min(h, w)
    if h < w:
        new_h, new_w = min_size, scale * w
    else:
        new_h, new_w = scale * h, min_size
    if max(new_h, new_w) > max_size:
        scale = max_size * 1.0 / max(new_h, new_w)
        new_h, new_w = new_h * scale, new_w * scale
    new_h, new_w = int(new_h + 0.5), int(new_w + 0.5)
    if (new_h, new_w) == (h, w):
        return bgr, h, w

    from PIL import Image

    # detectron2 是對 RGB/BGR 陣列整塊做 PIL resize；通道順序不影響結果。
    pil = Image.fromarray(np.ascontiguousarray(bgr))
    pil = pil.resize((new_w, new_h), Image.BILINEAR)
    return np.asarray(pil), new_h, new_w


def to_chw_float(resized_bgr):
    """(H,W,3) uint8 BGR -> (3,H,W) float32。

    刻意不做 normalize：GeneralizedRCNN.preprocess_image 會做 PIXEL_MEAN/STD，
    而那段在 tracing 匯出時已經被包進圖裡了。
    """
    return np.ascontiguousarray(resized_bgr.transpose(2, 0, 1)).astype(np.float32)


def _bilinear_sample(src, sx, sy):
    """對 src (H,W) 做雙線性取樣，超出範圍補 0。

    等價於 torch.nn.functional.grid_sample(..., mode='bilinear',
    padding_mode='zeros', align_corners=False) 的取樣部分。
    """
    h, w = src.shape
    x0 = np.floor(sx).astype(np.int64)
    y0 = np.floor(sy).astype(np.int64)
    x1, y1 = x0 + 1, y0 + 1

    wa = (x1 - sx) * (y1 - sy)
    wb = (x1 - sx) * (sy - y0)
    wc = (sx - x0) * (y1 - sy)
    wd = (sx - x0) * (sy - y0)

    def gather(xi, yi):
        valid = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        xc = np.clip(xi, 0, w - 1)
        yc = np.clip(yi, 0, h - 1)
        return np.where(valid, src[yc, xc], 0.0)

    return (gather(x0, y0) * wa + gather(x0, y1) * wb
            + gather(x1, y0) * wc + gather(x1, y1) * wd)


def paste_roi_masks(roi_masks, boxes, orig_h, orig_w, threshold=MASK_THRESH):
    """把 Nx1xMxM 的 ROI mask 貼回原圖大小。

    複製 detectron2.layers.mask_ops._do_paste_mask 的做法：以「像素中心」
    (+0.5) 對應到 **浮點數 box** 的正規化座標，再做雙線性取樣。

    先前的實作是把 mask 直接 cv2.resize 到取整後的 box 大小，對齊方式不同，
    在細長的裂縫上 IoU 只有 0.85~0.92（compare_legacy.py 抓到的）。
    """
    n = roi_masks.shape[0]
    out = np.zeros((n, orig_h, orig_w), dtype=bool)
    if n == 0:
        return out

    roi = roi_masks
    if roi.ndim == 4:
        roi = roi[:, 0]  # (N, M, M)
    msize = roi.shape[-1]

    for i in range(n):
        x0, y0, x1, y1 = [float(v) for v in boxes[i]]
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            continue
        # 只在 box 的整數外框內取樣（等同 detectron2 的 skip_empty 最佳化）
        x0_i, y0_i = max(int(np.floor(x0)) - 1, 0), max(int(np.floor(y0)) - 1, 0)
        x1_i = min(int(np.ceil(x1)) + 1, orig_w)
        y1_i = min(int(np.ceil(y1)) + 1, orig_h)
        if x1_i <= x0_i or y1_i <= y0_i:
            continue

        img_y = np.arange(y0_i, y1_i, dtype=np.float32) + 0.5
        img_x = np.arange(x0_i, x1_i, dtype=np.float32) + 0.5
        gy = (img_y - y0) / (y1 - y0) * 2 - 1
        gx = (img_x - x0) / (x1 - x0) * 2 - 1

        # align_corners=False：正規化座標 -> 來源像素座標
        sy = ((gy + 1) * msize - 1) / 2
        sx = ((gx + 1) * msize - 1) / 2
        sx_g, sy_g = np.meshgrid(sx, sy)

        sampled = _bilinear_sample(roi[i].astype(np.float32), sx_g, sy_g)
        out[i, y0_i:y1_i, x0_i:x1_i] = sampled > threshold
    return out


def rescale_boxes(boxes, from_hw, to_hw):
    """把 resize 後座標系的 box 還原回原圖座標，並 clip 到影像範圍內。"""
    if boxes.size == 0:
        return boxes
    fh, fw = from_hw
    th, tw = to_hw
    sx, sy = tw / float(fw), th / float(fh)
    boxes = boxes.copy().astype(np.float32)
    boxes[:, 0::2] *= sx
    boxes[:, 1::2] *= sy
    np.clip(boxes[:, 0::2], 0, tw, out=boxes[:, 0::2])
    np.clip(boxes[:, 1::2], 0, th, out=boxes[:, 1::2])
    return boxes


# --------------------------------------------------------------------------
# 後端
# --------------------------------------------------------------------------


class _BaseBackend:
    name = "base"

    def infer(self, bgr):  # pragma: no cover - interface
        raise NotImplementedError


def _load_manifest():
    """匯出時寫下的輸出張量順序；沒有就用 detectron2 的排序規則推。"""
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    # TracingAdapter 把 Instances 的欄位依 key 字母排序後攤平，最後接 image_size。
    return {"outputs": ["pred_boxes", "pred_classes", "pred_masks", "scores", "image_size"]}


def _assemble(raw_outputs, order, resized_hw, orig_hw):
    """把攤平的輸出張量組回 Detections。"""
    named = {}
    for key, value in zip(order, raw_outputs):
        named[key] = np.asarray(value)

    boxes = named.get("pred_boxes", np.zeros((0, 4), np.float32)).astype(np.float32)
    scores = named.get("scores", np.zeros((0,), np.float32)).astype(np.float32)
    classes = named.get("pred_classes", np.zeros((0,), np.int64)).astype(np.int64)
    roi_masks = named.get("pred_masks", np.zeros((0, 1, 28, 28), np.float32))

    # 模型內部已套用 SCORE_THRESH_TEST；這裡再過一次是為了讓 CRACK_SCORE_THRESH
    # 能在執行期再收緊。用 > 以符合 detectron2 fast_rcnn 的語意。
    keep = scores > SCORE_THRESH
    boxes, scores, classes = boxes[keep], scores[keep], classes[keep]
    roi_masks = roi_masks[keep] if roi_masks.shape[0] else roi_masks

    # 順序必須跟 detector_postprocess 一致：scale -> clip -> 濾掉退化 box -> 貼 mask。
    boxes = rescale_boxes(boxes, resized_hw, orig_hw)
    nonempty = (boxes[:, 2] - boxes[:, 0] > 0) & (boxes[:, 3] - boxes[:, 1] > 0)
    boxes, scores, classes = boxes[nonempty], scores[nonempty], classes[nonempty]
    roi_masks = roi_masks[nonempty] if roi_masks.shape[0] else roi_masks

    masks = paste_roi_masks(roi_masks, boxes, orig_hw[0], orig_hw[1])
    return Detections(boxes=boxes, scores=scores, classes=classes, masks=masks)


class OnnxBackend(_BaseBackend):
    """最輕的後端：只需要 onnxruntime + numpy + opencv。"""

    name = "onnx"

    def __init__(self, path=ONNX_PATH):
        import onnxruntime as ort

        providers = []
        available = ort.get_available_providers()
        for p in ("CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"):
            if p in available:
                providers.append(p)
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(path, sess_options=opts, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.order = _load_manifest()["outputs"]
        _log(f"onnxruntime providers={self.session.get_providers()}")

    def infer(self, bgr):
        orig_h, orig_w = bgr.shape[:2]
        resized, rh, rw = resize_shortest_edge(bgr)
        chw = to_chw_float(resized)
        raw = self.session.run(None, {self.input_name: chw})
        return _assemble(raw, self.order, (rh, rw), (orig_h, orig_w))


class TorchScriptBackend(_BaseBackend):
    """中量後端：需要 torch，但不需要 detectron2，也不需要 C++ 編譯器。"""

    name = "torchscript"

    def __init__(self, path=TS_PATH):
        import torch

        # 一定要先 import torchvision：匯出的圖裡有 torchvision::nms（RPN 用），
        # 那是 C++ 端註冊的自訂運算元，沒 import 過就 torch.jit.load 會噴
        # "Unknown builtin op: torchvision::nms"。
        # 先前沒寫這行也「能跑」，是因為測試腳本剛好都有 import detectron2
        # 而它會連帶 import torchvision——在乾淨的部署環境就會炸。
        import torchvision  # noqa: F401

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = torch.jit.load(path, map_location=self.device)
        self.model.eval()
        self.order = _load_manifest()["outputs"]
        _log(f"torchscript device={self.device}")

    def infer(self, bgr):
        torch = self.torch
        orig_h, orig_w = bgr.shape[:2]
        resized, rh, rw = resize_shortest_edge(bgr)
        chw = torch.from_numpy(to_chw_float(resized)).to(self.device)
        with torch.no_grad():
            raw = self.model(chw)
        if torch.is_tensor(raw):
            raw = (raw,)
        raw = [t.detach().cpu().numpy() for t in raw]
        return _assemble(raw, self.order, (rh, rw), (orig_h, orig_w))


class Detectron2Backend(_BaseBackend):
    """原本的路徑。單例化之後延遲已經大幅改善，但依賴仍然最重。"""

    name = "detectron2"

    def __init__(self, weights=D2_WEIGHTS):
        import torch
        from detectron2 import config, model_zoo
        from detectron2.engine import DefaultPredictor

        cfg = config.get_cfg()
        cfg.merge_from_file(
            model_zoo.get_config_file("COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml")
        )
        cfg.MODEL.ROI_HEADS.NUM_CLASSES = len(CLASS_NAMES)
        cfg.MODEL.WEIGHTS = weights
        cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = SCORE_THRESH
        cfg.INPUT.MIN_SIZE_TEST = MIN_SIZE_TEST
        cfg.INPUT.MAX_SIZE_TEST = MAX_SIZE_TEST
        cfg.MODEL.DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
        self.torch = torch
        self.predictor = DefaultPredictor(cfg)
        _log(f"detectron2 device={cfg.MODEL.DEVICE}")

    def infer(self, bgr):
        with self.torch.no_grad():
            inst = self.predictor(bgr)["instances"].to("cpu")
        masks = (
            inst.pred_masks.numpy().astype(bool)
            if inst.has("pred_masks")
            else np.zeros((len(inst), bgr.shape[0], bgr.shape[1]), bool)
        )
        return Detections(
            boxes=inst.pred_boxes.tensor.numpy().astype(np.float32),
            scores=inst.scores.numpy().astype(np.float32),
            classes=inst.pred_classes.numpy().astype(np.int64),
            masks=masks,
        )


# --------------------------------------------------------------------------
# 單例
# --------------------------------------------------------------------------

_engine = None
_engine_lock = threading.Lock()


def _build_backend():
    order = (
        [BACKEND]
        if BACKEND in ("onnx", "torchscript", "detectron2")
        else ["onnx", "torchscript", "detectron2"]
    )
    errors = []
    for name in order:
        try:
            if name == "onnx":
                if not os.path.exists(ONNX_PATH):
                    raise FileNotFoundError(ONNX_PATH)
                return OnnxBackend()
            if name == "torchscript":
                if not os.path.exists(TS_PATH):
                    raise FileNotFoundError(TS_PATH)
                return TorchScriptBackend()
            if name == "detectron2":
                if not os.path.exists(D2_WEIGHTS):
                    raise FileNotFoundError(D2_WEIGHTS)
                return Detectron2Backend()
        except Exception as exc:  # noqa: BLE001 - 後端不可用就換下一個
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            _log(f"後端 {name} 不可用 -> {type(exc).__name__}: {exc}")
    raise RuntimeError("沒有可用的推論後端。\n" + "\n".join(errors))


class CrackEngine:
    def __init__(self):
        t0 = time.perf_counter()
        self.backend = _build_backend()
        self._lock = threading.Lock()
        _log(f"後端 {self.backend.name} 載入完成，耗時 {time.perf_counter() - t0:.2f}s")
        self._warmup()

    def _warmup(self):
        """先跑一張假圖，把 CUDA context / cuDNN autotune / lazy alloc 的成本
        付在啟動階段，而不是第一次真的要判別的時候。"""
        t0 = time.perf_counter()
        try:
            dummy = np.zeros((MIN_SIZE_TEST, MIN_SIZE_TEST, 3), np.uint8)
            self.backend.infer(dummy)
            _log(f"warmup 完成，耗時 {time.perf_counter() - t0:.2f}s")
        except Exception as exc:  # noqa: BLE001 - warmup 失敗不該擋住啟動
            _log(f"warmup 失敗（可忽略）: {exc}")

    def infer(self, bgr):
        if bgr is None:
            raise ValueError("infer() 收到 None 影像")
        if bgr.ndim == 2:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
        elif bgr.shape[2] == 4:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_BGRA2BGR)
        t0 = time.perf_counter()
        # 單一模型實例不是 thread-safe，序列化進入。
        with self._lock:
            det = self.backend.infer(np.ascontiguousarray(bgr))
        det.latency_ms = (time.perf_counter() - t0) * 1000.0
        return det


def get_engine():
    """取得 process 內共用的引擎（第一次呼叫才載入模型）。"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = CrackEngine()
    return _engine


def preload():
    """在背景執行緒預先載入模型，讓 UI 可以先開起來。"""
    threading.Thread(target=get_engine, name="crack-engine-preload", daemon=True).start()
