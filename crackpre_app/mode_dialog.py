"""進入系統時選擇工作模式。

用清單而不是大按鈕：清單可以把每個模式的說明並排看完再決定，
也方便之後增加模式而不用重排版面。

兩種模式在行為上有實質差異，不只是換個來源：

  圖片模式  有限批次，每一張都會處理，一張都不漏。適合事後檢視既有照片。
  連續模式  即時串流，處理不完的舊影格會被丟掉，永遠處理最新畫面。
            適合現場巡檢——你要知道「現在」牆上有沒有裂縫，不是三十秒前。

所以這個選擇放在進入系統時問，而不是做成一個容易誤按的開關。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
    QVBoxLayout,
)

MODE_IMAGE = "image"
MODE_CONTINUOUS = "continuous"

MODES = [
    (MODE_CONTINUOUS, "🎥  連續模式",
     "接 Tello、RTSP 串流或本機影片，持續偵測。\n"
     "處理不完的舊畫面會被丟掉，永遠分析最新的一格——延遲不會累積。\n"
     "可再選「只預覽」（畫面流暢、不跑模型，飛行用）或「開始辨識」。"),
    (MODE_IMAGE, "📁  圖片模式",
     "選擇圖片檔或資料夾，逐張分析。\n"
     "每一張都會處理，不會略過。適合事後檢視已拍好的照片。"),
]


class ModeDialog(QDialog):
    """回傳 MODE_IMAGE 或 MODE_CONTINUOUS；使用者取消則 exec() 回傳 Rejected。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CrackPre — 進入系統")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._mode = None

        title = QLabel("要用哪種方式進行裂縫偵測？")
        title.setStyleSheet("font-size:15px;font-weight:bold;")

        self.list = QListWidget()
        self.list.setSpacing(2)
        for key, label, desc in MODES:
            item = QListWidgetItem(f"{label}\n{desc}")
            item.setData(Qt.UserRole, key)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        # 雙擊直接進入，不用再按確定
        self.list.itemDoubleClicked.connect(lambda _: self._accept())

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("進入系統")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addWidget(self.list, 1)
        layout.addSpacing(6)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

        self.list.setFocus()

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self._mode = item.data(Qt.UserRole)
        self.accept()

    @property
    def mode(self):
        return self._mode

    @staticmethod
    def ask(parent=None):
        """顯示對話框，回傳選到的模式；取消回傳 None。"""
        dlg = ModeDialog(parent)
        if dlg.exec() == QDialog.Accepted:
            return dlg.mode
        return None
