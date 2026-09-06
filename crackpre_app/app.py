"""桌面應用進入點。

啟動流程：先問模式（圖片／連續），再依模式開主視窗。
按取消就不開視窗，直接結束。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from . import config
from .gui import MainWindow
from .mode_dialog import MODE_CONTINUOUS, MODE_IMAGE, ModeDialog


def _demo_frames() -> list:
    """載入幾張既有影像，讓「示範影像」來源開箱可用。"""
    import cv2

    frames = []
    raw = Path(config.resource_path("data/raw"))
    if not raw.exists():
        return frames
    for path in sorted(raw.rglob("*.jpg"))[:200]:
        if path.stem.endswith("pre"):
            continue
        img = cv2.imread(str(path))
        if img is not None:
            frames.append(img)
        if len(frames) >= 10:
            break
    return frames


def _selftest() -> int:
    """無介面煙霧測試：確認視窗建得起來、兩種模式都能開、偵測跑得動。"""
    import os
    import time

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication(sys.argv)

    frames = _demo_frames()
    if not frames:
        print("selftest FAIL: 找不到示範影像")
        return 2

    # 模式對話框
    dlg = ModeDialog()
    assert dlg.mode is None
    dlg._choose(MODE_IMAGE)
    if dlg.mode != MODE_IMAGE:
        print("selftest FAIL: 模式對話框沒有回傳選擇")
        return 3

    # 連續模式視窗（用示範影像來源）
    win = MainWindow(mode=MODE_CONTINUOUS, demo_frames=frames)
    win.show()
    demo_idx = [i for i in range(win.source_box.count())
                if win.source_box.itemData(i) == "demo"]
    if not demo_idx:
        print("selftest FAIL: 沒有示範影像來源")
        return 4
    win.source_box.setCurrentIndex(demo_idx[0])
    win.threshold.setValue(1)          # 門檻壓低，確保會產生命中
    win._on_start()

    t0 = time.time()
    while time.time() - t0 < 180 and win.gallery.count() < 1:
        app.processEvents()
        time.sleep(0.02)
    hits = win.gallery.count()
    processed = win._processed
    win._on_stop()
    for _ in range(2000):
        app.processEvents()
        if win._thread is None:
            break
        time.sleep(0.02)
    win.close()

    # 圖片模式：真的跑一批圖，確認每一張都被處理（不丟幀）
    raw = Path(config.resource_path("data/raw"))
    paths = [str(p) for p in sorted(raw.rglob("*.jpg"))
             if not p.stem.endswith("pre")][:3]
    win2 = MainWindow(mode=MODE_IMAGE, demo_frames=frames)
    win2.show()
    has_pickers = hasattr(win2, "pick_files_btn") and hasattr(win2, "pick_dir_btn")
    img_processed = 0
    if paths:
        win2._selected_paths = paths
        win2.threshold.setValue(1)
        win2._on_start()
        t0 = time.time()
        while time.time() - t0 < 180 and win2._thread is not None:
            app.processEvents()
            time.sleep(0.02)
        img_processed = win2._processed
    win2.close()

    # 圖片模式的核心保證：選幾張就處理幾張
    exact = img_processed == len(paths)

    flight_ok = _selftest_flight(app, frames)

    ok = processed >= 1 and has_pickers and exact and flight_ok
    print(f"selftest {'OK' if ok else 'FAIL'}: "
          f"連續模式處理 {processed} 幀、命中 {hits} 張；"
          f"圖片模式選擇器 {'有' if has_pickers else '無'}、"
          f"處理 {img_processed}/{len(paths)} 張{'（相符）' if exact else '（不符！）'}；"
          f"飛行操控 {'通過' if flight_ok else '失敗'}")
    return 0 if ok else 5


def _selftest_flight(app, frames) -> bool:
    """驗證飛行操控的接線與按鍵對應，不需要真的有 Tello。

    連線相關的東西全部 mock 掉；這裡要確認的是速度狀態機、按鍵對應、
    面板顯示條件與 rc 節流設定，那些不接飛機也該是對的。
    """
    from PySide6.QtCore import Qt

    from .tello_control import RC_INTERVAL_MS, TelloController

    win = MainWindow(mode=MODE_CONTINUOUS, demo_frames=frames)
    win.show()
    app.processEvents()
    problems = []

    # 面板只在來源是 Tello 時顯示
    for i in range(win.source_box.count()):
        if win.source_box.itemData(i) == "rtsp":
            win.source_box.setCurrentIndex(i)
    app.processEvents()
    if win.flight_panel.isVisible():
        problems.append("RTSP 來源不該顯示飛行面板")
    for i in range(win.source_box.count()):
        if win.source_box.itemData(i) == "tello":
            win.source_box.setCurrentIndex(i)
    app.processEvents()
    if not win.flight_panel.isVisible():
        problems.append("Tello 來源應該顯示飛行面板")

    # rc 頻率不得超過 Tello SDK 建議的 20 Hz
    if RC_INTERVAL_MS < 50:
        problems.append(f"rc 間隔 {RC_INTERVAL_MS}ms 過快（應 >= 50ms）")

    # 按鍵 -> 速度對應（用假的 tello，不連線）
    ctl = TelloController()
    ctl._tello = object()            # 只是要讓 is_connected 為真
    ctl.speed = 60
    cases = [
        (Qt.Key_Up, "for_back", 60), (Qt.Key_Down, "for_back", -60),
        (Qt.Key_Right, "left_right", 60), (Qt.Key_Left, "left_right", -60),
        (Qt.Key_W, "up_down", 60), (Qt.Key_S, "up_down", -60),
        (Qt.Key_D, "yaw", 60), (Qt.Key_A, "yaw", -60),
    ]
    for key, field, want in cases:
        ctl.key_press(key)
        got = getattr(ctl, field)
        if got != want:
            problems.append(f"按下 {key}: {field}={got}，應為 {want}")
        ctl.key_release(key)
        if getattr(ctl, field) != 0:
            problems.append(f"放開 {key}: {field} 沒有歸零")

    # 未連線時不該送出任何指令
    ctl._tello = None
    ctl._send_rc()                   # 不該爆炸
    win.close()

    for p in problems:
        print(f"  飛行操控問題: {p}")
    return not problems


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()

    app = QApplication(sys.argv)

    # 依你的要求：開啟時先問要用哪種方式
    mode = ModeDialog.ask()
    if mode is None:
        return 0

    win = MainWindow(mode=mode, demo_frames=_demo_frames())
    win.show()

    missing = config.missing_required()
    if missing:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            win, "尚未完成設定",
            "以下設定還沒填，偵測與存檔仍可正常使用，\n"
            "但「上傳到 LINE」會失敗：\n\n  "
            + "\n  ".join(missing)
            + "\n\n可從右上角「設定」補上。")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
