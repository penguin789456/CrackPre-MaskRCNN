"""連續影像判別用的單槽 worker。

原本每按一次拍照鍵就 threading.Thread(target=PreImg)，在連續判別的情境下
會同時開出 N 個執行緒，每個都各自建一份模型 —— GPU 記憶體先炸，沒炸也會
互相搶 GIL，把 RTSP 的 appsink callback 一起拖住，畫面就卡住了。

這裡改成一條固定的推論執行緒 + 深度為 1 的「最新一幀」插槽：
新的 frame 進來就覆蓋掉還沒處理的舊 frame。連續模式下我們要的是最新畫面，
不是把每一幀都排隊處理完 —— 排隊只會讓延遲越積越大。
"""

from __future__ import annotations

import threading
import time


class LatestFrameWorker:
    """只保留最新一幀的背景處理器。

    :param handler: callable(frame, meta) -> None，在 worker 執行緒中呼叫。
    :param min_interval: 兩次處理之間的最小間隔（秒），用來限制送圖頻率。
    """

    def __init__(self, handler, min_interval=0.0, name="crack-worker"):
        self._handler = handler
        self._min_interval = float(min_interval)
        self._slot = None            # (frame, meta)
        self._cond = threading.Condition()
        self._stopping = False
        self._busy = False
        self.dropped = 0
        self.processed = 0
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    @property
    def busy(self):
        with self._cond:
            return self._busy or self._slot is not None

    def submit(self, frame, meta=None):
        """送出一幀。若前一幀還沒處理完就會被覆蓋。回傳是否覆蓋了舊幀。"""
        with self._cond:
            replaced = self._slot is not None
            if replaced:
                self.dropped += 1
            # copy 是必要的：GStreamer / VideoCapture 的 buffer 會被回收重用。
            self._slot = (frame.copy(), meta)
            self._cond.notify()
        return replaced

    def _run(self):
        last = 0.0
        while True:
            with self._cond:
                while self._slot is None and not self._stopping:
                    self._cond.wait()
                if self._stopping:
                    return
                frame, meta = self._slot
                self._slot = None
                self._busy = True

            try:
                gap = self._min_interval - (time.perf_counter() - last)
                if gap > 0:
                    time.sleep(gap)
                self._handler(frame, meta)
                self.processed += 1
            except Exception as exc:  # noqa: BLE001 - 單幀失敗不該終止 worker
                print(f"[pipeline] 處理影格失敗: {type(exc).__name__}: {exc}")
            finally:
                last = time.perf_counter()
                with self._cond:
                    self._busy = False

    def stop(self, timeout=5):
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        self._thread.join(timeout)
