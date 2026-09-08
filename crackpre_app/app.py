"""桌面應用進入點。

啟動流程：先問模式（圖片／連續），再依模式開主視窗。
按取消就不開視窗，直接結束。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
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


_MSGBOX_SAVED = {}


def _patch_msgbox(sink: list) -> None:
    """把 QMessageBox 的彈窗攔下來記進 sink，測試才不會卡在對話框。"""
    from PySide6.QtWidgets import QMessageBox

    for name in ("warning", "information", "critical"):
        _MSGBOX_SAVED[name] = getattr(QMessageBox, name)
        setattr(QMessageBox, name,
                staticmethod(lambda *a, _s=sink, **k: _s.append(" ".join(
                    str(x) for x in a if isinstance(x, str)))))


def _unpatch_msgbox() -> None:
    from PySide6.QtWidgets import QMessageBox

    for name, fn in _MSGBOX_SAVED.items():
        setattr(QMessageBox, name, fn)
    _MSGBOX_SAVED.clear()


def _controller_handled_keys(ctl) -> set:
    """TelloController.key_press 自己會處理的按鍵。"""
    from PySide6.QtCore import Qt

    handled = set()
    saved = ctl._tello
    ctl._tello = object()
    try:
        for key in (Qt.Key_T, Qt.Key_L, Qt.Key_Up):
            if ctl.key_press(key):
                handled.add(key)
            ctl.key_release(key)
    finally:
        ctl._tello = saved
    return handled


def _select_run_mode(win, preview: bool) -> None:
    """在行為下拉裡明確選「只預覽」或「開始辨識」。

    測試不該依賴下拉的預設順序——預設值改了，測試會靜默地測錯東西。
    """
    for i in range(win.run_mode_box.count()):
        if bool(win.run_mode_box.itemData(i)) is preview:
            win.run_mode_box.setCurrentIndex(i)
            return
    raise AssertionError(f"行為下拉找不到 preview={preview} 的項目")


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

    # 模式對話框：兩個按鈕，按下去要回傳對應的模式
    dlg = ModeDialog()
    if set(dlg.buttons_by_mode) != {MODE_IMAGE, MODE_CONTINUOUS}:
        print(f"selftest FAIL: 模式按鈕不對 {set(dlg.buttons_by_mode)}")
        return 3
    dlg.buttons_by_mode[MODE_IMAGE].click()
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
    _select_run_mode(win, preview=False)   # 明確選辨識，不要依賴下拉的預設值
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
    snap_ok = _selftest_snapshot(app, frames)
    preview_ok, preview_fps = _selftest_preview(app, frames)
    model_ok, backend_name = _selftest_model(app, frames)
    thread_ok = _selftest_threading(app, frames)

    ok = (processed >= 1 and has_pickers and exact and flight_ok
          and snap_ok and preview_ok and model_ok and thread_ok)
    print(f"selftest {'OK' if ok else 'FAIL'}: "
          f"連續模式處理 {processed} 幀、命中 {hits} 張；"
          f"圖片模式選擇器 {'有' if has_pickers else '無'}、"
          f"處理 {img_processed}/{len(paths)} 張{'（相符）' if exact else '（不符！）'}；"
          f"飛行操控 {'通過' if flight_ok else '失敗'}；"
          f"拍一張 {'通過' if snap_ok else '失敗'}；"
          f"預覽 {'通過' if preview_ok else '失敗'}（{preview_fps:.0f} fps）；"
          f"啟動載入 {'通過' if model_ok else '失敗'}（{backend_name}）；"
          f"執行緒 {'通過' if thread_ok else '失敗'}")
    return 0 if ok else 5


def _selftest_model(app, frames):
    """驗證啟動時載入模型，以及設定裡的引擎選擇。"""
    import time

    import crack_engine

    problems = []

    # 從乾淨狀態開始，確認 start_model_load() 真的會去載
    crack_engine.reset_engine()
    win = MainWindow(mode=MODE_CONTINUOUS, demo_frames=frames)
    win.show()
    if crack_engine.is_loaded():
        problems.append("reset_engine() 之後不該還是已載入狀態")
    if "尚未載入" not in win.model_status.text():
        problems.append(f"初始狀態文字不對：{win.model_status.text()}")

    win.start_model_load()
    t0 = time.time()
    while time.time() - t0 < 180 and not crack_engine.is_loaded():
        app.processEvents()
        time.sleep(0.02)
    for _ in range(200):                 # 讓 loaded signal 送達
        app.processEvents()
        time.sleep(0.01)

    backend = crack_engine.loaded_backend_name() or "?"
    if not crack_engine.is_loaded():
        problems.append("啟動載入沒有把模型載起來")
    elif "就緒" not in win.model_status.text():
        problems.append(f"載入完成後狀態沒更新：{win.model_status.text()}")

    # 設定畫面要能選引擎，且不可用的後端要被停用而不是藏起來
    from .settings_dialog import SettingsDialog

    dlg = SettingsDialog(win)
    if dlg.backend_box.count() != 1 + len(crack_engine.BACKEND_FILES):
        problems.append(f"引擎下拉項目數不對：{dlg.backend_box.count()}")
    if dlg.backend_box.findData("auto") < 0:
        problems.append("引擎下拉缺少「自動」")
    avail = crack_engine.available_backends()
    for name, (ok_flag, _reason) in avail.items():
        idx = dlg.backend_box.findData(name)
        if idx < 0:
            problems.append(f"引擎下拉缺少 {name}")
            continue
        enabled = dlg.backend_box.model().item(idx).isEnabled()
        if enabled != ok_flag:
            problems.append(f"{name} 可用={ok_flag} 但選單 enabled={enabled}")
    if dlg.values().get("CRACK_BACKEND") is None:
        problems.append("設定沒有輸出 CRACK_BACKEND")
    dlg.close()

    win.close()
    for p in problems:
        print(f"  啟動載入問題: {p}")
    return not problems, backend


def _selftest_threading(app, frames) -> bool:
    """背景工作的回呼必須回到主執行緒才能碰 GUI。

    這是實際踩到的當機：回呼接的是區域 closure，PySide 判斷不出接收端執行緒，
    退回 DirectConnection，於是 QMessageBox 在 worker 執行緒被建立，噴出
    QObject::setParent / QBasicTimer::stop 然後整個卡住。
    接成主執行緒 QObject 的 bound method 才會走 QueuedConnection。
    """
    import threading
    import time

    from PySide6.QtCore import QObject, QThread as _QThread, Signal, Slot

    problems = []
    main_id = threading.get_ident()
    win = MainWindow(mode=MODE_CONTINUOUS, demo_frames=frames)
    win.show()

    # 1) 靜態檢查：回呼必須是 bound method。接 closure 就是當機的成因。
    for name in ("_on_upload_done", "_on_upload_failed",
                 "_on_analyze_done", "_on_analyze_failed"):
        fn = getattr(win, name, None)
        if fn is None or not hasattr(fn, "__self__"):
            problems.append(f"{name} 不是 MainWindow 的 bound method")

    # 2) 執行期檢查：worker 執行緒發出的訊號，接到主執行緒 QObject 的
    #    bound method 時，必須在主執行緒被執行（QueuedConnection）。
    class _Emitter(QObject):
        fire = Signal()

        @Slot()
        def run(self):
            self.fire.emit()

    class _Receiver(QObject):
        def __init__(self):
            super().__init__()
            self.tid = None

        @Slot()
        def on_fire(self):
            self.tid = threading.get_ident()

    thread = _QThread(win)          # 給 parent，避免被 GC
    emitter = _Emitter()
    receiver = _Receiver()          # 沒 moveToThread -> 屬於主執行緒
    emitter.moveToThread(thread)
    thread.started.connect(emitter.run)
    emitter.fire.connect(receiver.on_fire)
    emitter.fire.connect(thread.quit)
    thread.start()

    t0 = time.time()
    while time.time() - t0 < 10 and receiver.tid is None:
        app.processEvents()
        time.sleep(0.005)
    thread.quit()
    thread.wait(3000)

    if receiver.tid is None:
        problems.append("worker 訊號沒有送達")
    elif receiver.tid != main_id:
        problems.append(
            f"回呼在 worker 執行緒執行（tid={receiver.tid}，主={main_id}）"
            "——這樣碰 GUI 會當掉")

    win.close()
    for p in problems:
        print(f"  執行緒問題: {p}")
    return not problems


def _selftest_preview(app, frames):
    """驗證「只預覽」真的把畫面更新率跟推論解耦。

    這不是看有沒有按鈕，而是實際量測：預覽跑兩秒應該產生數十格，
    而同樣時間跑辨識（CPU 上每格數秒）連一格都不一定跑得完。
    """
    import time

    problems = []

    win = MainWindow(mode=MODE_CONTINUOUS, demo_frames=frames)
    win.show()
    demo_idx = [i for i in range(win.source_box.count())
                if win.source_box.itemData(i) == "demo"]
    if not demo_idx:
        return True, 0.0
    win.source_box.setCurrentIndex(demo_idx[0])

    _select_run_mode(win, preview=True)
    win._on_start()
    if win.run_mode_box.isEnabled() or win.start_btn.isEnabled():
        problems.append("執行中「行為下拉」與「連線」都該反灰")

    window_s = 2.0
    t0 = time.time()
    while time.time() - t0 < window_s:
        app.processEvents()
        time.sleep(0.005)
    n = win._processed
    fps = n / window_s

    if not win._preview_only:
        problems.append("_preview_only 沒有被設起來")
    # 推論在 CPU 上每格數秒；預覽若沒解耦，兩秒內不可能有幾十格
    if n < 10:
        problems.append(f"預覽 {window_s:.0f} 秒只有 {n} 格，看起來沒有跟推論解耦")
    if win._last_result is not None and win._last_result.latency_ms != 0.0:
        problems.append("預覽的影格不該有推論耗時")
    if win._last_result is not None and win._last_result.score != 0.0:
        problems.append("預覽的影格不該有信心度")

    win._on_stop()
    for _ in range(2000):
        app.processEvents()
        if win._thread is None:
            break
        time.sleep(0.02)
    if not win.run_mode_box.isEnabled() or not win.start_btn.isEnabled():
        problems.append("停止後下拉與連線鈕都該恢復")
    win.close()

    # 圖片模式不該有「只預覽」
    win2 = MainWindow(mode=MODE_IMAGE, demo_frames=frames)
    win2.show()
    app.processEvents()
    if win2.run_mode_box.isVisible():
        problems.append("圖片模式不該顯示行為下拉")
    win2.close()

    for p in problems:
        print(f"  預覽問題: {p}")
    return not problems, fps


def _selftest_snapshot(app, frames) -> bool:
    """驗證「拍一張」：低於門檻也要存得下來，且圖片模式沒有這個按鈕。"""
    import os
    import time

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    problems = []

    win = MainWindow(mode=MODE_CONTINUOUS, demo_frames=frames)
    win.show()
    demo_idx = [i for i in range(win.source_box.count())
                if win.source_box.itemData(i) == "demo"]
    if not demo_idx:
        return True                       # 沒有示範來源就沒得測
    win.source_box.setCurrentIndex(demo_idx[0])
    # 門檻設到最高：自動存檔不會觸發，命中清單裡出現的只可能是手動拍的
    win.threshold.setValue(100)
    _select_run_mode(win, preview=False)   # 辨識模式：P 走「已分析、立即存」那條
    if win.snapshot_btn.isEnabled():
        problems.append("尚未開始時「拍一張」不該可按")
    win._on_start()

    t0 = time.time()
    while time.time() - t0 < 180 and not win.snapshot_btn.isEnabled():
        app.processEvents()
        time.sleep(0.02)

    if not win.snapshot_btn.isEnabled():
        problems.append("有畫面之後「拍一張」仍不可按")
    else:
        before = win.gallery.count()
        # 走鍵盤路徑，順便驗 P 有沒有接上
        win.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_P, Qt.NoModifier))
        app.processEvents()
        if win.gallery.count() != before + 1:
            problems.append(f"按 P 沒有新增命中（{before} -> {win.gallery.count()}）")
        else:
            item = win.gallery.item(0)
            path = item.data(Qt.UserRole)
            if not (path and os.path.exists(path)):
                problems.append("命中檔案沒有實際寫出")
            if "📷" not in item.text():
                problems.append("手動擷取沒有標記")

    win._on_stop()
    for _ in range(2000):
        app.processEvents()
        if win._thread is None:
            break
        time.sleep(0.02)
    if win.snapshot_btn.isEnabled():
        problems.append("停止後「拍一張」不該可按")
    win.close()

    # 圖片模式不該有這個按鈕出現在控制列
    win2 = MainWindow(mode=MODE_IMAGE, demo_frames=frames)
    win2.show()
    app.processEvents()
    if win2.snapshot_btn.isVisible():
        problems.append("圖片模式不該顯示「拍一張」")
    win2.close()

    for p in problems:
        print(f"  拍一張問題: {p}")
    return not problems


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

    # 面板不該再有「連線」鈕——控制列的「連線」已經包含建立 Tello 連線
    if hasattr(win, "connect_btn"):
        problems.append("飛行面板不該再有獨立的「連線」鈕")
    if not hasattr(win, "disconnect_btn"):
        problems.append("飛行面板缺少「中斷連線」")
    # 起飛鈕可按（才顯示得出提示訊息），但沒開影像時按下去必須擋掉
    if not win.takeoff_btn.isEnabled():
        problems.append("「起飛」該可按，才有機會顯示前置條件提示")
    if win.disconnect_btn.isEnabled():
        problems.append("未連線時「中斷連線」不該可按")

    # 關鍵安全行為：沒有影像串流時，起飛必須被擋下，不能自作主張開串流或起飛
    took_off = []
    saved_takeoff = win._tello.takeoff
    saved_start = win._on_start
    started = []
    win._tello.takeoff = lambda: took_off.append(True)
    win._on_start = lambda *a, **k: started.append(True)
    boxes = []
    _patch_msgbox(boxes)
    try:
        win._on_takeoff()                      # 此時 _thread is None（沒開影像）
    finally:
        _unpatch_msgbox()
        win._tello.takeoff = saved_takeoff
        win._on_start = saved_start
    if took_off:
        problems.append("沒有影像串流時仍然起飛了")
    if started:
        problems.append("起飛不該自動開啟影像串流")
    if not any("影像串流" in b for b in boxes):
        problems.append(f"沒有顯示「尚未開啟影像串流」提示：{boxes}")

    # T 快速鍵必須走同一條檢查，不能繞過
    if Qt.Key_T in _controller_handled_keys(win._tello):
        problems.append("T 仍由 TelloController 直接處理，會繞過起飛前置檢查")
    # 兩種停止的標示要能分辨
    if "影像" not in win.stop_btn.text():
        problems.append(f"停止鈕沒有標明是影像：{win.stop_btn.text()}")
    if "中斷" not in win.disconnect_btn.text():
        problems.append(f"中斷鈕標示不清：{win.disconnect_btn.text()}")

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

    # 先把整個 GUI 建好（含載入示範影像），再跳 modal 讓使用者進入系統。
    # 這樣選完模式視窗立刻就在，不會出現「選完之後畫面還要再等一下」的空窗。
    frames = _demo_frames()
    holder = MainWindow(mode=MODE_CONTINUOUS, demo_frames=frames)

    mode = ModeDialog.ask(holder)
    if mode is None:
        holder.close()
        return 0

    # 兩種模式的控制列組成不同，所以選定後才決定要用哪個視窗
    win = holder if mode == MODE_CONTINUOUS else \
        MainWindow(mode=mode, demo_frames=frames)
    if win is not holder:
        holder.close()
    win.show()

    # 啟動就開始載模型。載入含 warmup 要好幾秒，等到使用者按「連線」才開始
    # 的話那幾秒會被當成當掉。狀態顯示在畫面下方。
    win.start_model_load()

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
