"""不需要模型權重的自我檢查。

驗證 detectron2-free 的那幾段（前處理 / 後處理 / 畫圖 / 單槽 worker）行為正確。
只需要 numpy + opencv。

    python selftest.py
"""

from __future__ import annotations

# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---

import os
import sys
import threading
import time

import cv2
import numpy as np

import crack_engine as ce
import paths
from pipeline import LatestFrameWorker
from visualize import draw_detections

failures = []
skipped = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def skip(name, reason):
    """略過而非失敗。

    LINE / ImgBB 金鑰是選用的——全新安裝還沒設定 .env 時，偵測與存檔照樣能用，
    所以那些檢查不該讓整份驗證變成紅的。
    """
    print(f"  SKIP  {name}　（{reason}）")
    skipped.append(name)


def test_resize():
    print("resize_shortest_edge")
    # 720p：短邊 720 -> 800，長邊 1280*(800/720)=1422 > 1333，故再縮到 1333
    out, h, w = ce.resize_shortest_edge(np.zeros((720, 1280, 3), np.uint8), 800, 1333)
    check("720p 受 max_size 限制", (h, w) == (750, 1333), f"got {(h, w)}")
    check("回傳影像尺寸一致", out.shape[:2] == (h, w), f"got {out.shape[:2]}")

    # 正方形：800x800，短邊剛好 800，不需縮放
    out, h, w = ce.resize_shortest_edge(np.zeros((800, 800, 3), np.uint8), 800, 1333)
    check("已符合尺寸時不重採樣", (h, w) == (800, 800) and out.shape[:2] == (800, 800))

    # 直式：短邊是寬。900->800 會讓長邊變成 1422，超過 max_size 所以再縮回 1333。
    out, h, w = ce.resize_shortest_edge(np.zeros((1600, 900, 3), np.uint8), 800, 1333)
    check("直式影像以寬為短邊且受 max_size 限制", (h, w) == (1333, 750), f"got {(h, w)}")

    # 短邊放大：小圖要被 upscale 到 min_size
    out, h, w = ce.resize_shortest_edge(np.zeros((240, 320, 3), np.uint8), 800, 1333)
    check("小圖放大至短邊 800", (h, w) == (800, 1067), f"got {(h, w)}")


def test_chw():
    print("to_chw_float")
    img = np.random.randint(0, 255, (32, 48, 3), np.uint8)
    chw = ce.to_chw_float(img)
    check("形狀為 (3,H,W)", chw.shape == (3, 32, 48), f"got {chw.shape}")
    check("dtype 為 float32", chw.dtype == np.float32)
    check("未做正規化（值域保持 0-255）", np.allclose(chw[0], img[:, :, 0]))


def test_rescale_boxes():
    print("rescale_boxes")
    boxes = np.array([[10.0, 20.0, 110.0, 220.0]], np.float32)
    out = ce.rescale_boxes(boxes, from_hw=(400, 200), to_hw=(800, 400))
    check("等比放大 2 倍", np.allclose(out, [[20, 40, 220, 440]]), f"got {out}")

    # 超出邊界要被 clip
    out = ce.rescale_boxes(np.array([[-5.0, -5.0, 500.0, 500.0]], np.float32),
                           from_hw=(400, 400), to_hw=(400, 400))
    check("clip 到影像範圍", np.allclose(out, [[0, 0, 400, 400]]), f"got {out}")

    check("空輸入不爆炸", ce.rescale_boxes(np.zeros((0, 4), np.float32),
                                          (10, 10), (20, 20)).shape == (0, 4))


def test_paste_masks():
    print("paste_roi_masks")
    # 全 1 的 ROI mask 應該填滿整個 box
    roi = np.ones((1, 1, 28, 28), np.float32)
    boxes = np.array([[10.0, 20.0, 60.0, 100.0]], np.float32)
    masks = ce.paste_roi_masks(roi, boxes, 200, 300)
    check("輸出形狀為 (N,H,W)", masks.shape == (1, 200, 300), f"got {masks.shape}")
    check("box 內為 True", masks[0, 25, 30])
    check("box 外為 False", not masks[0, 5, 5])
    inside = masks[0, 20:100, 10:60].sum()
    check("覆蓋整個 box", inside == 80 * 50, f"got {inside} want {80 * 50}")

    # 半邊為 0 的 ROI mask
    roi = np.zeros((1, 1, 28, 28), np.float32)
    roi[0, 0, :, 14:] = 1.0
    masks = ce.paste_roi_masks(roi, np.array([[0.0, 0.0, 56.0, 56.0]], np.float32), 56, 56)
    check("mask 左半為空", not masks[0, 28, 5])
    check("mask 右半為實", masks[0, 28, 50])

    check("空輸入不爆炸", ce.paste_roi_masks(np.zeros((0, 1, 28, 28), np.float32),
                                            np.zeros((0, 4), np.float32), 10, 10).shape == (0, 10, 10))


def test_assemble():
    print("_assemble（輸出張量組回 Detections）")
    order = ["pred_boxes", "pred_classes", "pred_masks", "scores", "image_size"]
    raw = [
        np.array([[10.0, 10.0, 50.0, 50.0], [0.0, 0.0, 20.0, 20.0]], np.float32),
        np.array([0, 2], np.int64),
        np.ones((2, 1, 28, 28), np.float32),
        np.array([0.9, 0.1], np.float32),  # 第二筆低於 SCORE_THRESH=0.5
        np.array([100, 100], np.int64),
    ]
    det = ce._assemble(raw, order, resized_hw=(100, 100), orig_hw=(200, 200))
    check("低分結果被濾掉", len(det) == 1, f"got {len(det)}")
    check("box 依比例還原", np.allclose(det.boxes[0], [20, 20, 100, 100]), f"got {det.boxes}")
    check("class 保留", det.classes[0] == 0)
    check("mask 尺寸對上原圖", det.masks.shape == (1, 200, 200), f"got {det.masks.shape}")
    check("best_score 正確", abs(det.best_score - 0.9) < 1e-6)


def test_draw():
    print("draw_detections")
    img = np.full((200, 200, 3), 30, np.uint8)
    det = ce.Detections(
        boxes=np.array([[20.0, 20.0, 120.0, 120.0]], np.float32),
        scores=np.array([0.93], np.float32),
        classes=np.array([1], np.int64),
        masks=np.zeros((1, 200, 200), bool),
    )
    det.masks[0, 30:100, 30:100] = True
    out = draw_detections(img, det)
    check("輸出尺寸不變", out.shape == img.shape)
    check("未就地修改輸入", np.all(img == 30))
    check("有畫上東西", not np.array_equal(out, img))
    check("mask 區域被上色", not np.array_equal(out[50, 50], img[50, 50]))

    empty = ce.Detections()
    check("0 偵測時原樣回傳", np.array_equal(draw_detections(img, empty), img))


def test_worker():
    print("LatestFrameWorker")
    seen = []
    gate = threading.Event()

    def handler(frame, meta):
        seen.append(meta)
        if meta == "slow":
            gate.wait(2.0)

    w = LatestFrameWorker(handler, min_interval=0.0, name="selftest-worker")
    w.submit(np.zeros((4, 4, 3), np.uint8), "slow")
    time.sleep(0.15)  # 確保 worker 已經接手第一幀
    # 塞三幀進來，只有最後一幀該被留下
    for tag in ("a", "b", "c"):
        w.submit(np.zeros((4, 4, 3), np.uint8), tag)
    gate.set()
    time.sleep(0.4)
    w.stop()
    check("舊幀被覆蓋而非排隊", seen == ["slow", "c"], f"got {seen}")
    check("dropped 計數正確", w.dropped == 2, f"got {w.dropped}")

    # copy 語意：submit 之後改動原陣列不該影響已排入的幀
    got = []
    w2 = LatestFrameWorker(lambda f, m: got.append(f[0, 0, 0]), name="selftest-worker2")
    buf = np.zeros((4, 4, 3), np.uint8)
    w2.submit(buf, None)
    buf[:] = 255  # 模擬 VideoCapture 重用 buffer
    time.sleep(0.3)
    w2.stop()
    check("submit 會複製影格", got == [0], f"got {got}")


def test_settings():
    print("settings / .env 讀取")
    import settings

    if settings.ENV_PATH is None or not os.path.exists(settings.ENV_PATH):
        # 全新安裝的正常狀態。LINE 上傳才需要金鑰，偵測本身不需要。
        skip("整組 .env 檢查", "尚未建立 .env，見 README 步驟 4；不影響偵測功能")
        # 沒有 .env 也該安全地回傳空值而不是爆炸
        check("無 .env 時 load_channels() 回傳空 list",
              settings.load_channels() == [], f"got {settings.load_channels()}")
        check("無 .env 時 describe() 仍可執行", isinstance(settings.describe(), str))
        check("無 .env 時 missing_required() 會列出缺項",
              len(settings.missing_required()) > 0)
        return

    check("找到 .env", os.path.exists(settings.ENV_PATH),
          f"ENV_PATH={settings.ENV_PATH}")
    check("讀到 LINE token", bool(settings.LINE_CHANNEL_ACCESS_TOKEN))
    check("讀到 LINE channel secret", bool(settings.LINE_CHANNEL_SECRET))

    channels = settings.load_channels()
    check("索引 0 為 None（Channel 從 1 起算）", channels and channels[0] is None,
          f"got {channels[:1]}")
    check("Channel 1 是使用者/群組 ID",
          len(channels) > 1 and channels[1] and channels[1][0] in "UC",
          f"got {channels[1] if len(channels) > 1 else None}")
    check("LINE_CHANNEL_ACCESS_TOKEN 不會被誤判成 Channel",
          all(c is None or c[0] in "UC" for c in channels),
          f"got {channels}")
    check("describe() 遮蔽金鑰",
          settings.LINE_CHANNEL_ACCESS_TOKEN not in settings.describe())


def test_env_parser():
    print("settings 的 .env parser")
    import tempfile

    import settings

    body = (
        "# 註解\n"
        "\n"
        "PLAIN=value1\n"
        'QUOTED="has spaces"\n'
        "SINGLE='single'\n"
        "export EXPORTED=value2\n"
        "TRAILING=value3  # 行內註解\n"
        'HASHKEY="abc#def"\n'
        "EMPTY=\n"
        "NOEQUALS\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False,
                                     encoding="utf-8") as f:
        f.write(body)
        path = f.name
    try:
        v = settings._parse_env_file(path)
        check("一般值", v.get("PLAIN") == "value1", f"got {v.get('PLAIN')!r}")
        check("雙引號去除", v.get("QUOTED") == "has spaces", f"got {v.get('QUOTED')!r}")
        check("單引號去除", v.get("SINGLE") == "single", f"got {v.get('SINGLE')!r}")
        check("export 前綴", v.get("EXPORTED") == "value2", f"got {v.get('EXPORTED')!r}")
        check("行內註解移除", v.get("TRAILING") == "value3", f"got {v.get('TRAILING')!r}")
        check("引號內的 # 保留", v.get("HASHKEY") == "abc#def", f"got {v.get('HASHKEY')!r}")
        check("空值", v.get("EMPTY") == "", f"got {v.get('EMPTY')!r}")
        check("無等號的行忽略", "NOEQUALS" not in v)
    finally:
        os.unlink(path)


def test_uploader_guard():
    print("uploader 前置檢查")
    import uploader

    saved = uploader.settings.IMGBB_API_KEY
    try:
        uploader.settings.IMGBB_API_KEY = ""
        try:
            uploader.upload("whatever.jpg")
            check("沒有 API key 時應報錯", False, "沒有丟出例外")
        except uploader.UploadError as exc:
            check("沒有 API key 時報錯且訊息可讀", "IMGBB_API_KEY" in str(exc), str(exc))

        uploader.settings.IMGBB_API_KEY = "dummy"
        try:
            uploader.upload("does_not_exist_12345.jpg")
            check("檔案不存在時應報錯", False, "沒有丟出例外")
        except uploader.UploadError as exc:
            check("檔案不存在時報錯", "找不到" in str(exc), str(exc))
    finally:
        uploader.settings.IMGBB_API_KEY = saved


def test_end_to_end():
    """用假後端跑完整條路徑：推論 -> 畫圖 -> 存檔 -> 通知。不需要模型權重。"""
    print("端到端（假後端）")
    import shutil

    import CRACKpre
    import notifier

    class FakeBackend:
        name = "fake"
        score = 0.93

        def infer(self, bgr):
            h, w = bgr.shape[:2]
            masks = np.zeros((1, h, w), bool)
            masks[0, 50:150, 50:150] = True
            return ce.Detections(
                boxes=np.array([[50.0, 50.0, 150.0, 150.0]], np.float32),
                scores=np.array([self.score], np.float32),
                classes=np.array([2], np.int64),
                masks=masks,
            )

    engine = ce.CrackEngine.__new__(ce.CrackEngine)   # 略過 __init__，不載入真模型
    backend = FakeBackend()
    engine.backend = backend
    engine._lock = threading.Lock()
    saved_engine, ce._engine = ce._engine, engine
    saved_deliver = notifier._deliver
    sent = []
    notifier._deliver = lambda p, m, c: sent.append((p, m, c))

    tmp = os.path.join(paths.DATA_RAW, "_selftest")
    try:
        os.makedirs(tmp, exist_ok=True)
        img = np.full((300, 400, 3), 40, np.uint8)

        annotated, det = CRACKpre.PreImgArray(img, channel=2,
                                              save_prefix=os.path.join(tmp, "t1"))
        check("回傳偵測結果", len(det) == 1)
        check("有量到延遲", det.latency_ms > 0)
        check("回傳標註後影像", not np.array_equal(annotated, img))
        check("高信心時存檔", os.path.exists(os.path.join(tmp, "t1pre.jpg")))
        notifier.flush(timeout=5)
        check("高信心時送通知", len(sent) == 1 and sent[0][2] == 2, f"got {sent}")
        check("通知訊息格式沿用舊版",
              sent and sent[0][1] == "The crack model confidence is over 80% in 93%",
              f"got {sent[0][1] if sent else None}")

        # 舊介面：傳入不含副檔名的前綴
        cv2.imwrite(os.path.join(tmp, "t2.jpg"), img)
        out = CRACKpre.PreImg(os.path.join(tmp, "t2"), 1)
        check("PreImg 舊介面可用", out is not None)
        check("PreImg 一定產生 *pre.jpg", os.path.exists(os.path.join(tmp, "t2pre.jpg")))
        check("PreImg 檔案不存在時回 None", CRACKpre.PreImg(os.path.join(tmp, "nope"), 1) is None)

        # 低於警報門檻
        notifier.flush(timeout=5)
        before = len(sent)
        backend.score = 0.55
        CRACKpre.PreImgArray(img, channel=2, save_prefix=os.path.join(tmp, "t3"))
        notifier.flush(timeout=5)
        check("低信心不送通知", len(sent) == before, f"got {len(sent) - before} 則")
        check("低信心不存檔", not os.path.exists(os.path.join(tmp, "t3pre.jpg")))
    finally:
        ce._engine = saved_engine
        notifier._deliver = saved_deliver
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print(f"OpenCV {cv2.__version__} / NumPy {np.__version__}\n")
    for fn in (test_resize, test_chw, test_rescale_boxes, test_paste_masks,
               test_assemble, test_draw, test_worker, test_settings,
               test_env_parser, test_uploader_guard, test_end_to_end):
        fn()
        print()
    if failures:
        print(f"{len(failures)} 項失敗: {failures}")
        sys.exit(1)
    if skipped:
        print(f"全部通過（{len(skipped)} 項略過：{'、'.join(skipped)}）。")
    else:
        print("全部通過。")


if __name__ == "__main__":
    main()
