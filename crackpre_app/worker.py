"""QThread 偵測 worker。

把 DetectionEngine 移出 UI 執行緒，用 Qt signal 把影格／命中／錯誤送回 GUI，
介面才不會卡住。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .engine import DetectionEngine
from .sources import CaptureBackend


class ModelLoader(QObject):
    """在背景把模型載起來。

    改成啟動時就載，而不是等第一次偵測才載：載入含 warmup 要好幾秒，
    等到使用者按下「連線」才開始，那幾秒會被誤認為當掉。
    """

    loaded = Signal(str)      # 實際用到的後端名稱
    failed = Signal(str)

    @Slot()
    def run(self) -> None:
        try:
            from crack_engine import get_engine

            self.loaded.emit(get_engine().backend.name)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class SnapshotWorker(QObject):
    """對「單一影格」跑一次推論。

    預覽模式下按「拍一張」用的：畫面是原始影像（沒跑過模型），所以要現場分析
    那一格。推論在 CPU 上要好幾秒，不能擋住 UI 執行緒，因此丟到背景。

    直接用 crack_engine 的單例，它內部有鎖，就算偵測正在跑也不會衝突。
    """

    done = Signal(object, object)   # (overlay, Detections)
    failed = Signal(str)

    def __init__(self, frame) -> None:
        super().__init__()
        self._frame = frame

    @Slot()
    def run(self) -> None:
        try:
            from crack_engine import get_engine
            from visualize import draw_detections

            det = get_engine().infer(self._frame)
            overlay = draw_detections(self._frame, det) if len(det) else self._frame
            self.done.emit(overlay, det)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class DetectionWorker(QObject):
    frame_ready = Signal(object)   # FrameResult
    hit_ready = Signal(object)     # HitRecord
    finished = Signal(int)         # 命中數
    error = Signal(str)

    def __init__(self, engine: DetectionEngine, backend: CaptureBackend) -> None:
        super().__init__()
        self._engine = engine
        self._backend = backend

    @Slot()
    def run(self) -> None:
        try:
            n = self._engine.run(
                self._backend,
                on_frame=self.frame_ready.emit,
                on_hit=self.hit_ready.emit,
            )
            self.finished.emit(n)
        except Exception as exc:  # 一律回報到 UI，不要讓執行緒直接死掉
            import traceback

            traceback.print_exc()
            self.error.emit(f"{type(exc).__name__}: {exc}")

    def stop(self) -> None:
        self._engine.stop()
