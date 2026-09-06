"""QThread 偵測 worker。

把 DetectionEngine 移出 UI 執行緒，用 Qt signal 把影格／命中／錯誤送回 GUI，
介面才不會卡住。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .engine import DetectionEngine
from .sources import CaptureBackend


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
