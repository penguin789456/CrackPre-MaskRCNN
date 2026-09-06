"""偵測引擎：把 CaptureBackend 的影格送進 Mask R-CNN，產生畫面與命中紀錄。

與 GUI 無關，Qt 的 worker 只是薄薄包一層，方便無介面測試。

兩種模式的差別就在這裡：

  圖片模式 (drop_stale=False)
      使用者挑的每一張都要處理，一張都不能漏。

  連續模式 (drop_stale=True)
      來源是即時串流，處理不完的舊影格要丟掉。獨立的讀取執行緒只保留最新
      一格，主迴圈永遠拿最新的那張。不這樣做延遲會一路累積，看到的結果會
      離現場越來越遠（bench_continuous.py 量過：排隊模式幀齡會漲到十秒以上）。

存檔一律先落地本機，不在偵測迴圈裡碰網路；要不要上傳 LINE 由使用者在命中
清單手動決定。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from .sources import CaptureBackend


@dataclass
class FrameResult:
    index: int
    score: float                 # 最高信心度，沒偵測到為 0
    count: int                   # 偵測到幾處
    overlay: np.ndarray          # 畫好標註的影像
    latency_ms: float
    label: str = ""
    age_ms: float = 0.0          # 影格從被讀到到開始處理之間的延遲


@dataclass
class HitRecord:
    path: str
    timestamp: str
    score: float
    label: str = ""
    uploaded: bool = False
    fields: dict = field(default_factory=dict)


class _LatestFrameReader:
    """背景讀取執行緒，只保留最新一格。

    對即時來源而言，read() 讀到的可能是解碼器 buffer 裡的舊資料；持續讀掉並
    只留最新的一格，才不會讓畫面與現實脫節。
    """

    def __init__(self, backend: CaptureBackend) -> None:
        self._backend = backend
        self._lock = threading.Lock()
        self._slot = None            # (frame, read_at)
        self._stop = threading.Event()
        self._ended = threading.Event()
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name="frame-reader",
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._backend.read()
            except Exception:
                self._ended.set()
                return
            if frame is None:
                self._ended.set()
                return
            with self._lock:
                if self._slot is not None:
                    self.dropped += 1
                self._slot = (frame, time.perf_counter())

    def take(self, timeout: float = 5.0):
        """取出最新一格；來源結束回 None。"""
        deadline = time.perf_counter() + timeout
        while True:
            with self._lock:
                slot = self._slot
                self._slot = None
            if slot is not None:
                return slot
            if self._ended.is_set() or self._stop.is_set():
                return None
            if time.perf_counter() > deadline:
                return None
            time.sleep(0.005)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


class DetectionEngine:
    def __init__(
        self,
        save_dir,
        threshold: float = 0.8,
        cooldown_s: float = 3.0,
        drop_stale: bool = True,
        min_interval_s: float = 0.0,
    ) -> None:
        self.save_dir = Path(save_dir)
        self.cooldown_s = float(cooldown_s)
        self.drop_stale = bool(drop_stale)
        self.min_interval_s = float(min_interval_s)
        self._threshold = float(threshold)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.dropped = 0

    # -- 執行期可調 -------------------------------------------------------

    @property
    def threshold(self) -> float:
        with self._lock:
            return self._threshold

    def set_threshold(self, value: float) -> None:
        with self._lock:
            self._threshold = float(value)

    def stop(self) -> None:
        self._stop.set()

    # -- 主迴圈 -----------------------------------------------------------

    def run(
        self,
        backend: CaptureBackend,
        on_frame: Optional[Callable[[FrameResult], None]] = None,
        on_hit: Optional[Callable[[HitRecord], None]] = None,
        now: Callable[[], float] = time.monotonic,
    ) -> int:
        """跑到來源結束或 stop() 被呼叫，回傳命中數。"""
        from crack_engine import get_engine
        from visualize import draw_detections

        self._stop.clear()
        self.dropped = 0
        engine = get_engine()

        backend.start()
        reader = None
        if self.drop_stale and backend.is_live:
            reader = _LatestFrameReader(backend)
            reader.start()

        idx = 0
        hits = 0
        last_save = -1e9
        last_run = -1e9
        try:
            while not self._stop.is_set():
                if reader is not None:
                    got = reader.take()
                    if got is None:
                        break
                    frame, read_at = got
                else:
                    frame = backend.read()
                    if frame is None:
                        break
                    read_at = time.perf_counter()

                # 連續模式下限制推論頻率，避免把 GPU/CPU 燒滿
                if self.min_interval_s:
                    gap = self.min_interval_s - (now() - last_run)
                    if gap > 0:
                        if self._stop.wait(gap):
                            break
                last_run = now()

                idx += 1
                age_ms = (time.perf_counter() - read_at) * 1000.0
                det = engine.infer(frame)
                overlay = draw_detections(frame, det) if len(det) else frame

                result = FrameResult(
                    index=idx,
                    score=det.best_score,
                    count=len(det),
                    overlay=overlay,
                    latency_ms=det.latency_ms,
                    label=backend.label(idx),
                    age_ms=age_ms,
                )
                if reader is not None:
                    self.dropped = reader.dropped
                if on_frame is not None:
                    on_frame(result)

                if det.best_score > self.threshold and \
                        (now() - last_save) >= self.cooldown_s:
                    hit = self._save(overlay, det.best_score, result.label)
                    last_save = now()
                    hits += 1
                    if on_hit is not None:
                        on_hit(hit)
        finally:
            if reader is not None:
                reader.stop()
            backend.release()
        return hits

    # -- 本機優先存檔 -----------------------------------------------------

    def _save(self, overlay: np.ndarray, score: float, label: str = "") -> HitRecord:
        self.save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now()
        stamp = ts.strftime("%Y%m%d_%H%M%S_%f")
        safe = "".join(c for c in label if c.isalnum() or c in "-_")[:40]
        name = f"crack_{stamp}{('_' + safe) if safe else ''}.jpg"
        path = self.save_dir / name
        cv2.imwrite(str(path), overlay)
        return HitRecord(path=str(path),
                         timestamp=ts.isoformat(timespec="seconds"),
                         score=score, label=label)
