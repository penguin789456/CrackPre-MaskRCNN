"""開啟時詢問要用哪種模式。

兩種模式在行為上有實質差異，不只是換個來源：

  圖片模式  有限批次，每一張都會處理，一張都不漏。適合事後檢視既有照片。
  連續模式  即時串流，處理不完的舊影格會被丟掉，永遠處理最新畫面。
            適合現場巡檢——你要知道「現在」牆上有沒有裂縫，不是三十秒前。

所以這個選擇放在啟動時問，而不是做成一個容易誤按的開關。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout,
)

MODE_IMAGE = "image"
MODE_CONTINUOUS = "continuous"


class ModeDialog(QDialog):
    """回傳 MODE_IMAGE 或 MODE_CONTINUOUS；使用者取消則 exec() 回傳 Rejected。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CrackPre — 選擇模式")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._mode = None

        title = QLabel("要用哪種方式進行裂縫偵測？")
        title.setStyleSheet("font-size:15px;font-weight:bold;")

        self.image_btn = QPushButton("📁  圖片模式")
        self.image_btn.setMinimumHeight(58)
        self.image_btn.setToolTip("挑選既有的圖片檔或整個資料夾，逐張分析")
        self.image_btn.clicked.connect(lambda: self._choose(MODE_IMAGE))

        image_desc = QLabel(
            "選擇圖片檔或資料夾，逐張分析。\n"
            "每一張都會處理，不會略過。適合事後檢視已拍好的照片。")
        image_desc.setStyleSheet("color:#888;")
        image_desc.setWordWrap(True)

        self.cont_btn = QPushButton("🎥  連續模式")
        self.cont_btn.setMinimumHeight(58)
        self.cont_btn.setToolTip("接無人機或 RTSP 串流，持續偵測最新畫面")
        self.cont_btn.clicked.connect(lambda: self._choose(MODE_CONTINUOUS))

        cont_desc = QLabel(
            "接 Tello、RTSP 串流或本機影片，持續偵測。\n"
            "處理不完的舊畫面會被丟掉，永遠分析最新的一格——延遲不會累積。")
        cont_desc.setStyleSheet("color:#888;")
        cont_desc.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addSpacing(10)
        layout.addWidget(self.image_btn)
        layout.addWidget(image_desc)
        layout.addSpacing(14)
        layout.addWidget(self.cont_btn)
        layout.addWidget(cont_desc)
        layout.addSpacing(10)
        layout.addWidget(buttons)
        self.setLayout(layout)

        self.image_btn.setFocus()

    def _choose(self, mode: str) -> None:
        self._mode = mode
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
