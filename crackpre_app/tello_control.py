"""Tello 手動飛行操控。

把 manual-control-pygame.py 的操控邏輯搬進 GUI。按鍵配置刻意維持一致，
飛手不用重新學：

    ↑ ↓        前進 / 後退
    ← →        左移 / 右移
    W / S      上升 / 下降
    A / D      左轉 / 右轉
    T          起飛
    L          降落

與舊版的兩個差異，都是刻意的：

1. **RC 送出頻率從 125 Hz 降到 20 Hz。**
   舊版用 `pygame.time.set_timer(USEREVENT, 1000 // 120)`，等於每 8 ms 送一次
   `send_rc_control`。Tello SDK 建議 rc 指令不要超過 20 Hz，灌太快會塞爆
   指令通道，反而讓起飛／降落這類需要回應的指令被拖延。

2. **連線與 Tello 實例由這裡持有**，CaptureBackend 只負責讀影格。
   否則偵測執行緒與 UI 執行緒會各自建一個 Tello，搶同一個 UDP 埠。

安全性：本模組不會自動起飛。停止偵測時只把速度歸零讓它懸停，不會擅自降落——
自動降落在半空中可能比懸停更危險。要降落一律由使用者明確操作。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

#: Tello SDK 建議的 rc 指令上限約 20 Hz
RC_INTERVAL_MS = 50

#: 狀態列更新頻率（讀的是狀態封包快取，不會送指令）
STATE_INTERVAL_MS = 1000

DEFAULT_SPEED = 60


class TelloController(QObject):
    """持有 Tello 連線，維護速度狀態並定期送出 rc 指令。"""

    connected = Signal()
    disconnected = Signal()
    state_changed = Signal(str)      # 給狀態列顯示的文字
    error = Signal(str)
    flying_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tello = None
        self._flying = False
        self.speed = DEFAULT_SPEED

        self.left_right = 0
        self.for_back = 0
        self.up_down = 0
        self.yaw = 0

        self._rc_timer = QTimer(self)
        self._rc_timer.setInterval(RC_INTERVAL_MS)
        self._rc_timer.timeout.connect(self._send_rc)

        self._state_timer = QTimer(self)
        self._state_timer.setInterval(STATE_INTERVAL_MS)
        self._state_timer.timeout.connect(self._poll_state)

    # -- 連線 -------------------------------------------------------------

    @property
    def tello(self):
        return self._tello

    @property
    def is_connected(self) -> bool:
        return self._tello is not None

    @property
    def is_flying(self) -> bool:
        return self._flying

    # 方法名刻意不叫 connect / disconnect：QObject 本身就有這兩個 Qt API，
    # 覆蓋掉會讓 signal 的 .connect() 派送到錯的地方（實測會直接 TypeError）。
    def open_connection(self) -> None:
        """連上 Tello 並開啟影像串流。失敗會丟例外。"""
        if self._tello is not None:
            return
        from djitellopy import Tello

        tello = Tello()
        tello.connect()                 # 沒回應會丟例外
        tello.set_speed(10)
        try:
            tello.streamoff()           # 上次沒關乾淨的話先關掉
        except Exception:
            pass
        tello.streamon()
        self._tello = tello
        self._state_timer.start()
        self.connected.emit()
        self._poll_state()

    def close_connection(self) -> None:
        self.stop_rc()
        self._state_timer.stop()
        if self._tello is not None:
            try:
                self._tello.streamoff()
            except Exception:
                pass
            try:
                self._tello.end()
            except Exception:
                pass
            self._tello = None
        self._flying = False
        self.disconnected.emit()

    # -- 飛行指令 ---------------------------------------------------------

    def takeoff(self) -> None:
        if self._tello is None:
            self.error.emit("尚未連線到 Tello")
            return
        try:
            self._tello.takeoff()
        except Exception as exc:
            self.error.emit(f"起飛失敗: {exc}")
            return
        self._flying = True
        self.flying_changed.emit(True)
        self.start_rc()

    def land(self) -> None:
        if self._tello is None:
            return
        self.stop_rc()
        try:
            self._tello.land()
        except Exception as exc:
            self.error.emit(f"降落失敗: {exc}")
        self._flying = False
        self.flying_changed.emit(False)

    def emergency(self) -> None:
        """立刻切斷馬達。機體會直接墜落，只在必要時使用。"""
        if self._tello is None:
            return
        self.stop_rc()
        try:
            self._tello.emergency()
        except Exception as exc:
            self.error.emit(f"緊急停止失敗: {exc}")
        self._flying = False
        self.flying_changed.emit(False)

    # -- rc 迴圈 ----------------------------------------------------------

    def start_rc(self) -> None:
        if self._tello is not None and not self._rc_timer.isActive():
            self._rc_timer.start()

    def stop_rc(self) -> None:
        self._rc_timer.stop()
        self.reset_velocities()
        # 補送一次全零，避免它照著最後一筆速度繼續飄
        if self._tello is not None and self._flying:
            try:
                self._tello.send_rc_control(0, 0, 0, 0)
            except Exception:
                pass

    def reset_velocities(self) -> None:
        self.left_right = self.for_back = self.up_down = self.yaw = 0

    def _send_rc(self) -> None:
        if self._tello is None:
            return
        try:
            self._tello.send_rc_control(
                self.left_right, self.for_back, self.up_down, self.yaw)
        except Exception:
            # rc 是每 50 ms 一次的高頻指令，偶爾掉一次不值得洗版
            pass

    def _poll_state(self) -> None:
        if self._tello is None:
            return
        try:
            # djitellopy 從狀態封包快取讀取，不會送指令，所以不會阻塞
            bat = self._tello.get_battery()
            h = self._tello.get_height()
            self.state_changed.emit(f"電量 {bat}%　高度 {h} cm")
        except Exception:
            self.state_changed.emit("狀態讀取失敗")

    # -- 鍵盤 -------------------------------------------------------------

    #: Qt key -> (欄位名, 倍率)
    def key_press(self, key) -> bool:
        """處理按下。有處理回 True。"""
        from PySide6.QtCore import Qt

        s = self.speed
        mapping = {
            Qt.Key_Up: ("for_back", s),
            Qt.Key_Down: ("for_back", -s),
            Qt.Key_Left: ("left_right", -s),
            Qt.Key_Right: ("left_right", s),
            Qt.Key_W: ("up_down", s),
            Qt.Key_S: ("up_down", -s),
            Qt.Key_A: ("yaw", -s),
            Qt.Key_D: ("yaw", s),
        }
        if key in mapping:
            field, value = mapping[key]
            setattr(self, field, value)
            return True
        # T / L 刻意不在這裡處理。起飛有前置條件（要先開影像串流）要檢查，
        # 那個檢查在 GUI 層；若這裡也接 T，快速鍵就會繞過檢查直接起飛。
        return False

    def key_release(self, key) -> bool:
        """處理放開：把對應軸歸零。"""
        from PySide6.QtCore import Qt

        groups = {
            Qt.Key_Up: "for_back", Qt.Key_Down: "for_back",
            Qt.Key_Left: "left_right", Qt.Key_Right: "left_right",
            Qt.Key_W: "up_down", Qt.Key_S: "up_down",
            Qt.Key_A: "yaw", Qt.Key_D: "yaw",
        }
        if key in groups:
            setattr(self, groups[key], 0)
            return True
        return False
