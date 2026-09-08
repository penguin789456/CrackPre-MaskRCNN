"""主視窗。

版面沿用 D:\\CrackPre 的 crackpre_app.gui：上方控制列、左邊即時畫面、右邊命中
清單（可開啟／上傳／刪除）。偵測跑在 QThread worker 上。

與那邊不同的是這裡有兩種模式，控制列會依模式改變：

    圖片模式  來源選擇器換成「選擇圖片／選擇資料夾」，並顯示處理進度。
    連續模式  來源選擇器提供 Tello / RTSP / 影片檔，並顯示丟幀數與幀齡。

上傳一律是手動的，偵測過程中永遠不會自動推播 LINE。
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)

from . import config
from .engine import DetectionEngine, HitRecord
from .mode_dialog import MODE_CONTINUOUS, MODE_IMAGE
from .sources import (
    CaptureBackend, FakeBackend, ImageBackend, RtspBackend, TelloBackend,
    VideoBackend,
)
from .tello_control import TelloController
from .worker import DetectionWorker

FLIGHT_KEYS = {
    Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right,
    Qt.Key_W, Qt.Key_S, Qt.Key_A, Qt.Key_D, Qt.Key_T, Qt.Key_L,
}

FLIGHT_HINT = ("↑↓ 前後　←→ 左右　W/S 升降　A/D 轉向　T 起飛　L 降落")

# 門檻滑桿 0..100 -> 0.00..1.00 的信心度
_THR_MAX = 100
_THR_SCALE = 100.0

IMAGE_FILTER = "影像檔 (*.jpg *.jpeg *.png *.bmp);;所有檔案 (*.*)"
VIDEO_FILTER = "影片檔 (*.mp4 *.avi *.mov *.mkv);;所有檔案 (*.*)"


def _to_pixmap(frame_bgr: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(img)


class MainWindow(QMainWindow):
    def __init__(self, mode: str = MODE_CONTINUOUS,
                 demo_frames=None) -> None:
        super().__init__()
        self.mode = mode
        self._demo_frames = demo_frames or []
        self._thread: QThread | None = None
        self._worker: DetectionWorker | None = None
        self._engine: DetectionEngine | None = None
        self._uploads = []           # 保留執行緒參考，避免被 GC
        self._selected_paths = []    # 圖片模式選到的檔案
        self._processed = 0
        self._last_result = None     # 最近一次的 FrameResult，供「拍一張」使用
        self._preview_only = False   # 目前是否為只預覽（不跑模型）
        self._snap_jobs = []         # 保留單格分析的執行緒參考，避免被 GC
        self.flight_panel = None     # _build_body 才會建，先佔位避免存取順序問題
        self._tello = TelloController(self)
        self._tello.error.connect(
            lambda m: QMessageBox.warning(self, "Tello", m))
        self._tello.state_changed.connect(self._on_tello_state)
        self._tello.flying_changed.connect(self._on_flying_changed)

        mode_name = "圖片模式" if mode == MODE_IMAGE else "連續模式"
        self.setWindowTitle(f"CrackPre {mode_name} — Mask R-CNN 裂縫偵測")
        self.resize(1060, 680)

        self._build_controls()
        self._build_body()

    # -- 版面 -------------------------------------------------------------

    def _build_controls(self) -> None:
        self.controls = QHBoxLayout()

        if self.mode == MODE_IMAGE:
            self.pick_files_btn = QPushButton("選擇圖片…")
            self.pick_files_btn.clicked.connect(self._on_pick_files)
            self.pick_dir_btn = QPushButton("選擇資料夾…")
            self.pick_dir_btn.clicked.connect(self._on_pick_dir)
            self.source_label = QLabel("尚未選擇")
            self.source_label.setStyleSheet("color:#888;")
            self.controls.addWidget(self.pick_files_btn)
            self.controls.addWidget(self.pick_dir_btn)
            self.controls.addWidget(self.source_label, 1)
        else:
            self.source_box = QComboBox()
            self.source_box.addItem("Tello EDU", "tello")
            self.source_box.addItem("RTSP 串流", "rtsp")
            self.source_box.addItem("影片檔", "video")
            if self._demo_frames:
                self.source_box.addItem("示範影像", "demo")
            if not TelloBackend.is_available():
                item = self.source_box.model().item(0)
                item.setEnabled(False)
                item.setToolTip("未安裝 djitellopy")
                self.source_box.setCurrentIndex(1)
            self.source_box.currentIndexChanged.connect(self._on_source_changed)

            self.source_arg = QLineEdit(RtspBackend.DEFAULT_URL)
            self.source_arg.setMinimumWidth(260)
            self.browse_btn = QPushButton("瀏覽…")
            self.browse_btn.clicked.connect(self._on_browse_video)
            self.browse_btn.setVisible(False)

            self.controls.addWidget(QLabel("來源:"))
            self.controls.addWidget(self.source_box)
            self.controls.addWidget(self.source_arg, 1)
            self.controls.addWidget(self.browse_btn)
            self._on_source_changed()

        self.threshold = QSlider(Qt.Horizontal)
        self.threshold.setRange(0, _THR_MAX)
        self.threshold.setValue(80)          # 0.80，沿用舊版的通知門檻
        self.threshold.setFixedWidth(120)
        self.threshold.valueChanged.connect(self._on_threshold)
        self.thr_label = QLabel()
        self._update_thr_label(self.threshold.value())

        # 只預覽：開攝影機但不跑模型。畫面更新率不再被推論綁死（CPU 上約
        # 0.3 fps），飛行時才看得到即時影像。
        self.preview_btn = QPushButton("只預覽")
        self.preview_btn.setToolTip(
            "只開攝影機、不跑辨識，畫面以來源原本的幀率顯示。\n"
            "用來對焦、取景、操控無人機。此時按 P 會單獨分析那一格。")
        self.preview_btn.clicked.connect(self._on_preview)

        self.start_btn = QPushButton("開始辨識")
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)

        # 手動擷取。舊的 manual-control-pygame.py 是按 1~9 拍單張，新版統一用 P。
        # 對所有連續來源都有效（Tello / RTSP / 影片），不是只有 Tello。
        self.snapshot_btn = QPushButton("拍一張 (P)")
        self.snapshot_btn.setToolTip(
            "不論信心度高低，立刻把目前畫面存進命中清單\n"
            "快速鍵 P（需先點一下畫面取得焦點）")
        self.snapshot_btn.clicked.connect(self._on_snapshot)
        self.snapshot_btn.setEnabled(False)

        self.settings_btn = QPushButton("設定")
        self.settings_btn.clicked.connect(self._on_settings)

        self.controls.addWidget(QLabel("門檻:"))
        self.controls.addWidget(self.threshold)
        self.controls.addWidget(self.thr_label)
        if self.mode != MODE_IMAGE:
            self.controls.addWidget(self.preview_btn)
        self.controls.addWidget(self.start_btn)
        self.controls.addWidget(self.stop_btn)
        if self.mode != MODE_IMAGE:
            # 圖片模式沒有「當前畫面」的概念，逐張跑完就結束了
            self.controls.addWidget(self.snapshot_btn)
        self.controls.addWidget(self.settings_btn)

    def _build_flight_panel(self) -> QGroupBox:
        """Tello 手動飛行操控。只有連續模式且來源是 Tello 時才顯示。"""
        box = QGroupBox("飛行操控")

        self.connect_btn = QPushButton("連線")
        self.connect_btn.clicked.connect(self._on_tello_connect)
        self.takeoff_btn = QPushButton("起飛 (T)")
        self.takeoff_btn.clicked.connect(self._tello.takeoff)
        self.land_btn = QPushButton("降落 (L)")
        self.land_btn.clicked.connect(self._tello.land)
        self.emergency_btn = QPushButton("緊急停止")
        self.emergency_btn.setStyleSheet("color:#b00;font-weight:bold;")
        self.emergency_btn.clicked.connect(self._on_emergency)

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(10, 100)
        self.speed_slider.setValue(self._tello.speed)
        self.speed_slider.setFixedWidth(110)
        self.speed_label = QLabel(str(self._tello.speed))
        self.speed_slider.valueChanged.connect(self._on_speed)

        self.tello_state = QLabel("未連線")
        self.tello_state.setStyleSheet("color:#888;")

        row1 = QHBoxLayout()
        row1.addWidget(self.connect_btn)
        row1.addWidget(self.takeoff_btn)
        row1.addWidget(self.land_btn)
        row1.addWidget(self.emergency_btn)
        row1.addSpacing(12)
        row1.addWidget(QLabel("速度:"))
        row1.addWidget(self.speed_slider)
        row1.addWidget(self.speed_label)
        row1.addSpacing(12)
        row1.addWidget(self.tello_state)
        row1.addStretch(1)

        hint = QLabel(FLIGHT_HINT + "　P 拍一張　（點一下畫面取得鍵盤焦點）")
        hint.setStyleSheet("color:#888;")

        col = QVBoxLayout()
        col.addLayout(row1)
        col.addWidget(hint)
        box.setLayout(col)
        return box                   # 按鈕狀態由 _sync_flight_panel 統一更新

    def _build_body(self) -> None:
        self.view = QLabel("閒置中")
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumSize(640, 480)
        self.view.setStyleSheet("background:#202020;color:#aaa;")
        self.status = QLabel("信心度: -")

        self.gallery = QListWidget()
        self.gallery.setFixedWidth(300)
        self.gallery.itemSelectionChanged.connect(self._on_selection)
        self.open_btn = QPushButton("開啟")
        self.upload_btn = QPushButton("上傳到 LINE")
        self.delete_btn = QPushButton("刪除")
        self.open_btn.clicked.connect(self._on_open)
        self.upload_btn.clicked.connect(self._on_upload)
        self.delete_btn.clicked.connect(self._on_delete)

        self.channel_box = QComboBox()
        self._refresh_channels()

        gallery_col = QVBoxLayout()
        gallery_col.addWidget(QLabel("命中結果（超過門檻）"))
        gallery_col.addWidget(self.gallery)
        chan_row = QHBoxLayout()
        chan_row.addWidget(QLabel("Channel:"))
        chan_row.addWidget(self.channel_box, 1)
        gallery_col.addLayout(chan_row)
        gbtns = QHBoxLayout()
        gbtns.addWidget(self.open_btn)
        gbtns.addWidget(self.upload_btn)
        gbtns.addWidget(self.delete_btn)
        gallery_col.addLayout(gbtns)

        left_col = QVBoxLayout()
        left_col.addWidget(self.view, 1)
        left_col.addWidget(self.status)

        body = QHBoxLayout()
        body.addLayout(left_col, 1)
        body.addLayout(gallery_col)

        root = QVBoxLayout()
        root.addLayout(self.controls)
        if self.mode != MODE_IMAGE:
            self.flight_panel = self._build_flight_panel()
            root.addWidget(self.flight_panel)
        else:
            self.flight_panel = None
        root.addLayout(body, 1)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

        # 讓畫面能接鍵盤：飛行操控要靠 keyPressEvent
        self.view.setFocusPolicy(Qt.StrongFocus)
        self.setFocusPolicy(Qt.StrongFocus)

        self._on_selection()
        if self.flight_panel is not None:
            self._sync_flight_panel()

    def _refresh_channels(self) -> None:
        self.channel_box.clear()
        channels = config.line_channels()
        for i, cid in enumerate(channels):
            if i == 0 or not cid:
                continue
            self.channel_box.addItem(f"Channel {i}  ({cid[:6]}…)", i)
        if self.channel_box.count() == 0:
            self.channel_box.addItem("（未設定）", -1)

    # -- Tello 飛行操控 ----------------------------------------------------

    def _sync_flight_panel(self) -> None:
        """只有來源選 Tello 時才顯示飛行操控。"""
        if self.flight_panel is None:
            return
        is_tello = self.source_box.currentData() == "tello"
        self.flight_panel.setVisible(is_tello)
        self._update_flight_buttons()

    def _update_flight_buttons(self) -> None:
        if self.flight_panel is None:
            return
        conn = self._tello.is_connected
        self.connect_btn.setText("已連線" if conn else "連線")
        self.connect_btn.setEnabled(not conn)
        self.takeoff_btn.setEnabled(conn and not self._tello.is_flying)
        self.land_btn.setEnabled(conn and self._tello.is_flying)
        self.emergency_btn.setEnabled(conn)

    def _on_tello_connect(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("連線中…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            self._tello.open_connection()
        except Exception as exc:
            QMessageBox.warning(
                self, "Tello 連線失敗",
                f"{type(exc).__name__}: {exc}\n\n"
                "請確認：\n"
                "• 電腦已連上 Tello 的 Wi-Fi\n"
                "• 沒有其他程式正在占用 UDP 8889")
        finally:
            QApplication.restoreOverrideCursor()
            self._update_flight_buttons()

    def _on_speed(self, value: int) -> None:
        self._tello.speed = value
        self.speed_label.setText(str(value))

    def _on_emergency(self) -> None:
        if QMessageBox.question(
                self, "緊急停止",
                "這會立刻切斷馬達，機體會直接墜落。\n確定要執行嗎？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) == QMessageBox.Yes:
            self._tello.emergency()

    def _on_tello_state(self, text: str) -> None:
        if self.flight_panel is not None:
            self.tello_state.setText(text)

    def _on_flying_changed(self, _flying: bool) -> None:
        self._update_flight_buttons()

    # -- 鍵盤：飛行操控 ----------------------------------------------------

    def _flight_active(self) -> bool:
        return (self.flight_panel is not None
                and self.flight_panel.isVisible()
                and self._tello.is_connected)

    def keyPressEvent(self, event) -> None:
        # P = 拍一張。放在飛行按鍵之前處理，因為它對所有連續來源都有效
        # （RTSP、影片檔也能用），不是只有接了 Tello 才有。
        # 擋掉 autoRepeat：按住 P 不該連續存出一堆檔。
        if (self.mode != MODE_IMAGE and event.key() == Qt.Key_P
                and not event.isAutoRepeat()):
            if self.snapshot_btn.isEnabled():
                self._on_snapshot()
            event.accept()
            return

        # autoRepeat 會在按住時連續觸發，速度已經是持續值，重複設定沒意義
        if (self._flight_active() and not event.isAutoRepeat()
                and event.key() in FLIGHT_KEYS):
            if self._tello.key_press(event.key()):
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if (self._flight_active() and not event.isAutoRepeat()
                and event.key() in FLIGHT_KEYS):
            if self._tello.key_release(event.key()):
                event.accept()
                return
        super().keyReleaseEvent(event)

    # -- 門檻 -------------------------------------------------------------

    def _update_thr_label(self, raw: int) -> None:
        self.thr_label.setText(f"{raw / _THR_SCALE:.2f}")

    def _on_threshold(self, raw: int) -> None:
        self._update_thr_label(raw)
        if self._engine is not None:
            self._engine.set_threshold(raw / _THR_SCALE)

    # -- 來源選擇 ---------------------------------------------------------

    def _on_source_changed(self, *_) -> None:
        kind = self.source_box.currentData()
        self.browse_btn.setVisible(kind == "video")
        self.source_arg.setEnabled(kind in ("rtsp", "video"))
        if getattr(self, "flight_panel", None) is not None:
            self._sync_flight_panel()
        if kind == "rtsp" and not self.source_arg.text().startswith("rtsp"):
            self.source_arg.setText(RtspBackend.DEFAULT_URL)
        elif kind == "video" and self.source_arg.text().startswith("rtsp"):
            self.source_arg.clear()
            self.source_arg.setPlaceholderText("選擇影片檔…")

    def _on_browse_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "選擇影片檔", "", VIDEO_FILTER)
        if path:
            self.source_arg.setText(path)

    def _on_pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "選擇圖片", "", IMAGE_FILTER)
        if paths:
            self._selected_paths = paths
            self.source_label.setText(f"已選 {len(paths)} 張圖片")

    def _on_pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "選擇資料夾")
        if not d:
            return
        exts = (".jpg", ".jpeg", ".png", ".bmp")
        paths = []
        for name in sorted(os.listdir(d)):
            if not name.lower().endswith(exts):
                continue
            # 略過先前產生的標註圖，否則會把結果再判一次
            if os.path.splitext(name)[0].endswith("pre"):
                continue
            paths.append(os.path.join(d, name))
        if not paths:
            QMessageBox.information(self, "沒有圖片", f"{d}\n裡面找不到影像檔。")
            return
        self._selected_paths = paths
        self.source_label.setText(f"{Path(d).name} — {len(paths)} 張圖片")

    def _make_backend(self) -> CaptureBackend:
        if self.mode == MODE_IMAGE:
            if not self._selected_paths:
                raise RuntimeError("請先選擇圖片或資料夾。")
            return ImageBackend(self._selected_paths)

        kind = self.source_box.currentData()
        if kind == "tello":
            # 已經手動連線的話沿用同一個實例，不要再開一條連線搶 UDP 8889
            if self._tello.is_connected:
                return TelloBackend(tello=self._tello.tello)
            return TelloBackend()
        if kind == "rtsp":
            return RtspBackend(self.source_arg.text().strip())
        if kind == "video":
            path = self.source_arg.text().strip()
            if not path:
                raise RuntimeError("請先選擇影片檔。")
            return VideoBackend(path, loop=True)
        return FakeBackend(self._demo_frames, loop=True)

    # -- 開始／停止 -------------------------------------------------------

    def _on_preview(self) -> None:
        """只開攝影機，不跑辨識。"""
        self._on_start(preview_only=True)

    def _on_start(self, checked=False, preview_only: bool = False) -> None:
        if self._thread is not None:
            return
        try:
            backend = self._make_backend()
        except Exception as exc:
            QMessageBox.warning(self, "無法開始", str(exc))
            return

        self._processed = 0
        self._preview_only = preview_only
        image_mode = self.mode == MODE_IMAGE
        self._engine = DetectionEngine(
            save_dir=config.hits_dir(),
            threshold=self.threshold.value() / _THR_SCALE,
            # 圖片模式每張都要處理，也不需要冷卻時間
            drop_stale=not image_mode,
            cooldown_s=0.0 if image_mode else 3.0,
            min_interval_s=0.0,
            preview_only=preview_only,
        )
        self._worker = DetectionWorker(self._engine, backend)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.hit_ready.connect(self._on_hit)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._thread.start()

        self._last_result = None
        self.start_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        # 第一格處理完才啟用；在那之前沒有畫面可以拍
        self.snapshot_btn.setEnabled(False)
        self._set_source_enabled(False)
        self.view.setText("連線中…" if preview_only else "模型載入中…")

    def _set_source_enabled(self, on: bool) -> None:
        if self.mode == MODE_IMAGE:
            self.pick_files_btn.setEnabled(on)
            self.pick_dir_btn.setEnabled(on)
        else:
            self.source_box.setEnabled(on)
            self.source_arg.setEnabled(on)
            self.browse_btn.setEnabled(on)

    def _on_stop(self) -> None:
        if self._worker is not None:
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("停止中…")
            self._worker.stop()

    def _on_finished(self, n: int) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
        self._engine = None
        self.start_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("停止")
        self.snapshot_btn.setEnabled(False)   # 停止後沒有即時畫面可拍
        self._preview_only = False
        self._set_source_enabled(True)
        self._update_flight_buttons()   # 偵測用的連線可能已經被釋放
        if self.mode == MODE_IMAGE:
            self.status.setText(
                f"完成：處理 {self._processed} 張，命中 {n} 張")

    def _on_error(self, msg: str) -> None:
        QMessageBox.warning(self, "偵測發生錯誤", msg)
        self._on_finished(0)

    # -- signal slots -----------------------------------------------------

    def _on_frame(self, fr) -> None:
        self._processed += 1
        self._last_result = fr
        if self.mode != MODE_IMAGE and not self.snapshot_btn.isEnabled():
            self.snapshot_btn.setEnabled(True)
        self.view.setPixmap(_to_pixmap(fr.overlay).scaled(
            self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

        if self._preview_only:
            self.status.setText(
                f"預覽中（未辨識）　第 {fr.index} 格　按 P 分析目前畫面")
            return

        parts = [f"信心度 {fr.score:.3f}", f"{fr.count} 處",
                 f"{fr.latency_ms:.0f} ms"]
        if self.mode == MODE_IMAGE:
            total = getattr(self._current_backend_total(), "total", None)
            parts.insert(0, f"第 {fr.index}/{total} 張" if total
                         else f"第 {fr.index} 張")
            if fr.label:
                parts.append(fr.label)
        else:
            dropped = self._engine.dropped if self._engine else 0
            parts.append(f"幀齡 {fr.age_ms:.0f} ms")
            parts.append(f"已丟 {dropped} 幀")
        self.status.setText("　".join(parts))

    def _current_backend_total(self):
        return getattr(self._worker, "_backend", None) if self._worker else None

    def _on_hit(self, hit: HitRecord) -> None:
        name = hit.label or Path(hit.path).name
        mark = "📷 " if hit.fields.get("manual") else ""
        item = QListWidgetItem(f"{mark}{name}　({hit.score:.2f})")
        item.setData(Qt.UserRole, hit.path)
        self.gallery.insertItem(0, item)

    # -- 手動擷取 ---------------------------------------------------------

    def _on_snapshot(self) -> None:
        """把目前畫面存進命中清單，不論信心度高低。

        取代舊 manual-control-pygame.py 按 1~9 拍單張的工作流。
        """
        if self._engine is None:
            QMessageBox.information(self, "拍一張", "請先按「開始」。")
            return
        fr = self._last_result
        if fr is None:
            QMessageBox.information(self, "拍一張", "還沒有畫面，等第一格處理完再試。")
            return

        if self._preview_only:
            # 預覽畫面沒跑過模型，要現場分析那一格。推論要好幾秒，丟背景做。
            self._analyze_one(fr)
            return

        try:
            hit = self._engine.save_snapshot(fr.overlay, fr.score, fr.label)
        except Exception as exc:
            QMessageBox.warning(self, "拍一張失敗", f"{type(exc).__name__}: {exc}")
            return
        hit.fields["manual"] = True
        self._on_hit(hit)
        self.status.setText(
            f"已擷取　信心度 {fr.score:.3f}　{fr.count} 處　-> {Path(hit.path).name}")

    def _analyze_one(self, fr) -> None:
        """預覽模式：對單一影格跑一次推論，完成後存進命中清單。"""
        from .worker import SnapshotWorker

        self.snapshot_btn.setEnabled(False)
        self.snapshot_btn.setText("分析中…")
        self.status.setText("分析目前畫面中…")

        frame = fr.overlay.copy()     # 預覽模式的 overlay 就是原始影格
        label = fr.label
        thread = QThread(self)
        task = SnapshotWorker(frame)
        task.moveToThread(thread)
        thread.started.connect(task.run)

        def finish():
            self.snapshot_btn.setText("拍一張 (P)")
            self.snapshot_btn.setEnabled(self._thread is not None)
            thread.quit()

        def done(overlay, det):
            try:
                hit = self._engine.save_snapshot(overlay, det.best_score, label) \
                    if self._engine is not None else None
            except Exception as exc:
                QMessageBox.warning(self, "拍一張失敗", f"{type(exc).__name__}: {exc}")
                finish()
                return
            if hit is not None:
                hit.fields["manual"] = True
                self._on_hit(hit)
                self.status.setText(
                    f"已分析並存檔　信心度 {det.best_score:.3f}　{len(det)} 處"
                    f"　-> {Path(hit.path).name}")
            finish()

        def failed(msg):
            QMessageBox.warning(self, "分析失敗", msg)
            finish()

        task.done.connect(done)
        task.failed.connect(failed)
        thread.start()
        self._snap_jobs.append((thread, task))

    # -- 命中清單操作（全部手動） ------------------------------------------

    def _selected_path(self):
        item = self.gallery.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_selection(self) -> None:
        has = self._selected_path() is not None
        self.open_btn.setEnabled(has)
        self.upload_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)

    def _on_open(self) -> None:
        path = self._selected_path()
        if path and os.path.exists(path):
            os.startfile(path)  # Windows 專用，本專案就是 Windows

    def _on_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        if SettingsDialog(self).exec():
            config.reload_env()
            self._refresh_channels()

    def _on_upload(self) -> None:
        path = self._selected_path()
        if not path:
            return
        channel = self.channel_box.currentData()
        if channel is None or channel < 0:
            QMessageBox.warning(self, "尚未設定 Channel",
                                "請先開啟「設定」填入 LINE Channel。")
            return

        from .upload import start_upload

        self.upload_btn.setEnabled(False)
        self.upload_btn.setText("上傳中…")

        def done(url: str) -> None:
            self.upload_btn.setEnabled(True)
            self.upload_btn.setText("上傳到 LINE")
            QMessageBox.information(self, "上傳完成", f"已推播到 Channel {channel}。\n{url}")

        def failed(msg: str) -> None:
            self.upload_btn.setEnabled(True)
            self.upload_btn.setText("上傳到 LINE")
            QMessageBox.warning(self, "上傳失敗",
                                f"{msg}\n\n請在「設定」確認 IMGBB_API_KEY 與 LINE 設定。")

        self._uploads.append(
            start_upload(path, "CrackPre 偵測到裂縫", channel, done, failed, self))

    def _on_delete(self) -> None:
        path = self._selected_path()
        row = self.gallery.currentRow()
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:
                QMessageBox.warning(self, "刪除失敗", str(exc))
                return
        if row >= 0:
            self.gallery.takeItem(row)
        self._on_selection()

    # -- 關閉 -------------------------------------------------------------

    def closeEvent(self, event) -> None:
        # 機體還在空中就關掉程式，等於留一台沒人控制的飛機。先問清楚。
        if self._tello.is_connected and self._tello.is_flying:
            answer = QMessageBox.question(
                self, "Tello 仍在飛行中",
                "要先降落再關閉嗎？\n\n"
                "「Yes」降落後關閉　「No」保持懸停並關閉　「Cancel」不關閉",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes)
            if answer == QMessageBox.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.Yes:
                self._tello.land()
            else:
                self._tello.stop_rc()   # 至少把速度歸零，不要讓它照舊速度飄走

        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
        if self._tello.is_connected:
            self._tello.close_connection()
        super().closeEvent(event)
