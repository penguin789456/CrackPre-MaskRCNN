"""影像來源後端。

一個小介面，讓偵測引擎與來源解耦。實作：
    FakeBackend    測試用，餵一疊固定影格
    ImageBackend   圖片模式：一批檔案跑完就結束
    VideoBackend   本機影片檔，可循環（沒有無人機時測連續模式用）
    TelloBackend   Tello EDU
    RtspBackend    RTSP 串流（OpenCV/FFmpeg；有 GStreamer 就優先用）
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


class CaptureBackend(ABC):
    """產生 BGR 影格。生命週期：start() -> read()* -> stop()/release()。"""

    name: str = "base"
    #: 連續來源會一直吐影格；有限來源（圖片、非循環影片）跑完就回 None
    is_live: bool = True

    @classmethod
    def is_available(cls) -> bool:
        return True

    @abstractmethod
    def start(self) -> None:
        """開啟來源，失敗要丟例外。"""

    @abstractmethod
    def read(self) -> Optional[np.ndarray]:
        """回傳下一格 BGR 影像；來源結束回 None。"""

    def label(self, index: int) -> str:
        """給這一格的顯示名稱，存檔時當檔名用。"""
        return f"frame_{index:06d}"

    def stop(self) -> None:
        """停止產生影格（可重複呼叫）。"""

    def release(self) -> None:
        self.stop()


class FakeBackend(CaptureBackend):
    """餵一疊預先準備好的影格。測試與 --selftest 用。"""

    name = "fake"

    def __init__(self, frames, loop: bool = False) -> None:
        self._frames = list(frames)
        self._loop = loop
        self._i = 0
        self._started = False
        self.is_live = loop

    def start(self) -> None:
        self._started = True
        self._i = 0

    def read(self) -> Optional[np.ndarray]:
        if not self._started:
            raise RuntimeError("backend not started")
        if self._i >= len(self._frames):
            if not self._loop or not self._frames:
                return None
            self._i = 0
        frame = self._frames[self._i]
        self._i += 1
        return frame

    def stop(self) -> None:
        self._started = False


class ImageBackend(CaptureBackend):
    """圖片模式：依序讀一批圖片檔，讀完就結束。"""

    name = "image"
    is_live = False

    def __init__(self, paths) -> None:
        self.paths = list(paths)
        self._i = 0

    def start(self) -> None:
        self._i = 0
        if not self.paths:
            raise RuntimeError("沒有選擇任何圖片")

    def read(self) -> Optional[np.ndarray]:
        import cv2

        # 跳過讀不進來的檔案，不要因為一張壞圖就中斷整批
        while self._i < len(self.paths):
            path = self.paths[self._i]
            self._i += 1
            img = cv2.imread(path)
            if img is not None:
                self._last = path
                return img
            print(f"[sources] 略過無法讀取的檔案: {path}")
        return None

    def label(self, index: int) -> str:
        path = getattr(self, "_last", None)
        if path:
            return os.path.splitext(os.path.basename(path))[0]
        return super().label(index)

    @property
    def total(self) -> int:
        return len(self.paths)


class VideoBackend(CaptureBackend):
    """本機影片檔。預設循環播放，方便長時間測試連續模式。"""

    name = "video"

    def __init__(self, path: str, loop: bool = True) -> None:
        self.path = path
        self.loop = loop
        self.is_live = loop
        self._cap = None

    def start(self) -> None:
        import cv2

        if not os.path.exists(self.path):
            raise RuntimeError(f"找不到影片檔: {self.path}")
        cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            raise RuntimeError(f"無法開啟影片檔: {self.path}")
        self._cap = cap

    def read(self) -> Optional[np.ndarray]:
        import cv2

        if self._cap is None:
            raise RuntimeError("backend not started")
        ok, frame = self._cap.read()
        if not ok:
            if not self.loop:
                return None
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
            if not ok:
                return None
        return frame

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()

    def release(self) -> None:
        self.stop()
        self._cap = None


class TelloBackend(CaptureBackend):
    """Tello EDU 影像串流（djitellopy）。

    可以傳入一個**已經連線**的 Tello 實例——手動飛行操控時 TelloController
    會持有連線，這裡就只負責讀影格。兩邊各自 Tello() 會搶同一個 UDP 埠
    （8889），第二個必定失敗。

    自己建立連線時（tello=None）才會在 release() 關閉它；外部傳進來的連線
    生命週期歸呼叫端管，這裡不會擅自關掉別人的飛機。
    """

    name = "tello"

    @classmethod
    def is_available(cls) -> bool:
        try:
            import djitellopy  # noqa: F401

            return True
        except Exception:
            return False

    def __init__(self, tello=None) -> None:
        self._tello = tello
        self._owns = tello is None

    def start(self) -> None:
        if self._tello is not None:
            return                      # 外部已經連好線了
        from djitellopy import Tello

        self._tello = Tello()
        self._tello.connect()
        self._tello.streamon()

    def read(self) -> Optional[np.ndarray]:
        import cv2

        if self._tello is None:
            raise RuntimeError("backend not started")
        frame = self._tello.get_frame_read().frame
        if frame is None:
            return None
        # djitellopy 給的是 RGB，全專案統一用 BGR
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    def stop(self) -> None:
        if self._tello is not None and self._owns:
            try:
                self._tello.streamoff()
            except Exception:
                pass

    def release(self) -> None:
        if not self._owns:
            return                      # 連線是別人的，不要動
        self.stop()
        if self._tello is not None:
            try:
                self._tello.end()
            except Exception:
                pass
            self._tello = None


class RtspBackend(CaptureBackend):
    """RTSP 串流。

    有 GStreamer 就優先用，否則退回 OpenCV 內建的 FFmpeg。opencv-python 的
    wheel 已經帶 H.265 解碼，所以沒裝 GStreamer 也能運作。
    """

    name = "rtsp"

    DEFAULT_URL = "rtsp://192.168.144.25:8554/main.264"

    def __init__(self, url: str = DEFAULT_URL, prefer_gstreamer: bool = True) -> None:
        self.url = url or self.DEFAULT_URL
        self.prefer_gstreamer = prefer_gstreamer
        self._cap = None

    @staticmethod
    def gstreamer_available() -> bool:
        try:
            import gi  # noqa: F401

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst  # noqa: F401

            return True
        except Exception:
            return False

    def start(self) -> None:
        import cv2

        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            "rtsp_transport;tcp|buffer_size;102400|max_delay;100000|fflags;nobuffer",
        )
        if self.prefer_gstreamer and self.gstreamer_available():
            pipeline = (
                f"rtspsrc location={self.url} latency=10 protocols=tcp ! "
                "rtph265depay ! avdec_h265 ! videoconvert ! "
                "video/x-raw,format=BGR ! appsink drop=true max-buffers=1"
            )
            cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        else:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            raise RuntimeError(f"無法連上 RTSP 來源: {self.url}")
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap

    def read(self) -> Optional[np.ndarray]:
        if self._cap is None:
            raise RuntimeError("backend not started")
        ok, frame = self._cap.read()
        return frame if ok else None

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()

    def release(self) -> None:
        self.stop()
        self._cap = None
