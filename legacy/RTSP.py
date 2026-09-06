"""RTSP 來源的裂縫偵測。

與舊版（RTSP_gstreamer.py，用 gi / PyGObject / GStreamer）相比：

  * 改用 OpenCV 內建的 FFmpeg backend 拉流。opencv-python 的 wheel 已經帶了
    H.265 解碼，所以 conda 那一整串 gstreamer / gst-plugins-* / pygobject /
    pycairo / glib / pango / harfbuzz / librsvg 依賴全部可以拿掉。
  * imshow / waitKey 回到主執行緒。舊版是在 GStreamer 的 appsink callback 裡
    呼叫 HighGUI，在 Windows 上會偶發卡死。
  * 抓幀與推論分離：抓幀執行緒永遠只留最新一幀，推論在單槽 worker 裡跑，
    畫面不會被推論拖住。

操作：
    1~9  以對應的 LINE Channel 送出目前這一幀
    c    連續判別開關（自動送最新畫面進模型）
    q    離開
"""

from __future__ import annotations

# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---

import os
import threading
import time

# 必須在建立 VideoCapture 之前設定：走 TCP、縮小 buffer，降低串流延遲。
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|buffer_size;102400|max_delay;100000|fflags;nobuffer",
)

import cv2  # noqa: E402
from datetime import datetime  # noqa: E402

import paths  # noqa: E402
from CRACKpre import PreImgArray  # noqa: E402
from crack_engine import preload  # noqa: E402
from pipeline import LatestFrameWorker  # noqa: E402
from visualize import draw_detections  # noqa: E402

RTSP_URL = os.getenv("CRACK_RTSP_URL", "rtsp://192.168.144.25:8554/main.264")
RAW_DIR = paths.raw_subdir("RTSP")

# 連續模式下兩次推論的最小間隔，避免無謂地把 GPU 燒滿。
CONTINUOUS_MIN_INTERVAL = float(os.getenv("CRACK_CONTINUOUS_INTERVAL", "0.2"))

KEY_TO_CHANNEL = {ord(str(i)): i for i in range(1, 10)}

_latest = {"frame": None}
_latest_lock = threading.Lock()
_stop = threading.Event()

# 最近一次的標註結果，畫在畫面右側當作回饋。
_overlay = {"image": None, "at": 0.0}


#: 來源是本機影片檔而非網路串流時，行為要不一樣：讀到結尾要循環播放，
#: 而且要照影片本身的 fps 節流，否則會用 CPU 全速跑完。
IS_FILE_SOURCE = os.path.exists(RTSP_URL)


def open_capture():
    if IS_FILE_SOURCE:
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            raise SystemExit(f"無法開啟影片檔: {RTSP_URL}")
        return cap
    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise SystemExit(f"無法連上 RTSP 來源: {RTSP_URL}")
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def reader_loop():
    """獨立執行緒持續抓幀，只保留最新一幀。

    不這樣做的話 FFmpeg 的 buffer 會越積越多，看到的畫面會離現實越來越遠。
    """
    cap = open_capture()
    fails = 0

    src_fps = cap.get(cv2.CAP_PROP_FPS) if IS_FILE_SOURCE else 0
    frame_interval = 1.0 / src_fps if src_fps and src_fps > 0 else 0.0
    if IS_FILE_SOURCE:
        print(f"[RTSP] 影片檔來源，將循環播放 @ {src_fps or 25:.0f} fps: {RTSP_URL}")

    while not _stop.is_set():
        ok, frame = cap.read()

        if not ok and IS_FILE_SOURCE:
            # 播到結尾就從頭再來，方便長時間測試
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
            if not ok:
                print("[RTSP] 影片檔讀取失敗，停止")
                _stop.set()
                break

        if not ok:
            fails += 1
            if fails > 50:
                print("[RTSP] 連續讀取失敗，重新連線…")
                cap.release()
                time.sleep(1.0)
                try:
                    cap = open_capture()
                except SystemExit as exc:
                    print(exc)
                    _stop.set()
                    return
                fails = 0
            continue

        fails = 0
        with _latest_lock:
            _latest["frame"] = frame

        if frame_interval:
            time.sleep(frame_interval)

    cap.release()


def handle_frame(frame, meta):
    """在 worker 執行緒中執行：跑模型、畫圖、（必要時）存檔與通知。"""
    channel, continuous = meta
    prefix = None
    if not continuous:
        # 手動拍照維持舊行為：原始幀也留一份在 raw/RTSP。
        prefix = _timestamp_prefix()
        cv2.imwrite(prefix + ".jpg", frame)
        print(f"Image saved: {prefix}.jpg")
    annotated, det = PreImgArray(
        frame,
        channel,
        save_prefix=prefix,
        save_always=not continuous,
    )
    if len(det) == 0:
        annotated = draw_detections(frame, det)
    _overlay["image"] = annotated
    _overlay["at"] = time.time()


def _timestamp_prefix():
    os.makedirs(RAW_DIR, exist_ok=True)
    return os.path.join(RAW_DIR, f"captured_image_{datetime.now():%Y%m%d_%H%M%S}")


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    preload()  # 背景載入模型，畫面可以先開起來

    threading.Thread(target=reader_loop, name="rtsp-reader", daemon=True).start()

    worker = LatestFrameWorker(handle_frame, min_interval=CONTINUOUS_MIN_INTERVAL)
    continuous = False
    channel = 1

    print(__doc__)
    try:
        while not _stop.is_set():
            with _latest_lock:
                frame = _latest["frame"]
            if frame is None:
                if cv2.waitKey(30) & 0xFF == ord("q"):
                    break
                continue

            display = frame
            recent = _overlay["image"]
            if recent is not None and time.time() - _overlay["at"] < 3.0:
                display = recent

            status = f"CH{channel}  {'CONTINUOUS' if continuous else 'MANUAL'}"
            if worker.busy:
                status += "  [inferring]"
            status += f"  processed={worker.processed} dropped={worker.dropped}"
            display = display.copy()
            cv2.putText(display, status, (10, 26), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.imshow("RTSP Stream", display)

            if continuous and not worker.busy:
                worker.submit(frame, (channel, True))

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("c"):
                continuous = not continuous
                print(f"[RTSP] 連續判別 {'開啟' if continuous else '關閉'}")
            elif key in KEY_TO_CHANNEL:
                channel = KEY_TO_CHANNEL[key]
                worker.submit(frame, (channel, False))
                print(f"[RTSP] 已送出一幀到 Channel {channel}")
    except KeyboardInterrupt:
        print("Stopped by user")
    finally:
        _stop.set()
        worker.stop()
        cv2.destroyAllWindows()
        import notifier

        notifier.flush(timeout=15)


if __name__ == "__main__":
    main()
