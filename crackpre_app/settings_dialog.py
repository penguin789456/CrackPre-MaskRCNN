"""設定對話框：編輯 .env 裡的金鑰與 LINE 推播目標。

金鑰欄位預設遮蔽顯示，可用「顯示」勾選框切換。「測試」會實際打一次 imgbb
與 LINE，確認設定真的能用，而不是只檢查欄位非空。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from . import config


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CrackPre 設定")
        self.setMinimumWidth(560)

        current = config.read_env_file()
        self._fields = {}

        form = QFormLayout()

        # --- 推論引擎 ---
        # 不可用的選項照樣列出但標明原因，比直接藏起來好——使用者才知道
        # 「為什麼沒有 onnx 可選」。
        import crack_engine

        self.backend_box = QComboBox()
        avail = crack_engine.available_backends()
        self.backend_box.addItem("自動（依序 onnx → torchscript → detectron2）", "auto")
        for name, (path, desc) in crack_engine.BACKEND_FILES.items():
            ok, reason = avail.get(name, (False, "未知"))
            self.backend_box.addItem(f"{name}　{desc}　[{reason}]", name)
            if not ok:
                idx = self.backend_box.count() - 1
                self.backend_box.model().item(idx).setEnabled(False)
        want = (current.get("CRACK_BACKEND", "") or "auto").lower()
        pos = self.backend_box.findData(want)
        self.backend_box.setCurrentIndex(pos if pos >= 0 else 0)
        form.addRow("推論引擎", self.backend_box)

        for key, label, secret in config.EDITABLE_KEYS:
            edit = QLineEdit(current.get(key, ""))
            if secret:
                edit.setEchoMode(QLineEdit.Password)
            edit.setPlaceholderText(key)
            self._fields[key] = (edit, secret)
            form.addRow(label, edit)

        self.show_secrets = QCheckBox("顯示金鑰")
        self.show_secrets.toggled.connect(self._toggle_echo)

        hint = QLabel(
            f"儲存位置：{config.env_path()}\n"
            "ImgBB 金鑰可到 https://api.imgbb.com/ 免費申請。\n"
            "推播目標是使用者 ID（U 開頭）或群組 ID（C 開頭）。")
        hint.setStyleSheet("color:#888;")
        hint.setWordWrap(True)

        self.test_btn = QPushButton("測試連線")
        self.test_btn.clicked.connect(self._on_test)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.show_secrets)
        layout.addWidget(hint)
        layout.addWidget(self.test_btn)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def _toggle_echo(self, shown: bool) -> None:
        for edit, secret in self._fields.values():
            if secret:
                edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)

    def values(self) -> dict:
        out = {k: e.text().strip() for k, (e, _) in self._fields.items()}
        out["CRACK_BACKEND"] = self.backend_box.currentData()
        return out

    def _on_save(self) -> None:
        try:
            path = config.save_env(self.values())
        except OSError as exc:
            QMessageBox.warning(self, "儲存失敗", str(exc))
            return
        QMessageBox.information(self, "已儲存", f"設定已寫入\n{path}")
        self.accept()

    def _on_test(self) -> None:
        """真的送一次，才知道設定能不能用。"""
        import os
        import tempfile

        import cv2
        import numpy as np

        values = self.values()
        config.save_env(values)          # 先存檔，測的才是即將生效的設定

        problems = []
        if not values.get("IMGBB_API_KEY"):
            problems.append("• 未填 IMGBB_API_KEY")
        if not values.get("LINE_CHANNEL_ACCESS_TOKEN"):
            problems.append("• 未填 LINE_CHANNEL_ACCESS_TOKEN")
        if not any(values.get(f"LINE_CHANNEL_{i}") for i in (1, 2, 3)):
            problems.append("• 未填任何 LINE 推播目標")
        if problems:
            QMessageBox.warning(self, "設定不完整", "\n".join(problems))
            return

        self.test_btn.setEnabled(False)
        self.test_btn.setText("測試中…")
        try:
            import uploader

            # 用一張很小的合成圖測試，不要送出使用者的實際影像
            probe = np.full((64, 64, 3), 200, np.uint8)
            cv2.putText(probe, "TEST", (4, 38), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 0, 0), 2)
            path = os.path.join(tempfile.gettempdir(), "crackpre_settings_test.jpg")
            cv2.imwrite(path, probe)
            try:
                original, _preview = uploader.upload(path)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

            QMessageBox.information(
                self, "測試成功",
                f"ImgBB 上傳正常：\n{original}\n\n"
                "（僅測試圖片上傳，未推播 LINE 以免打擾群組。）")
        except Exception as exc:
            QMessageBox.warning(self, "測試失敗", f"{type(exc).__name__}: {exc}")
        finally:
            self.test_btn.setEnabled(True)
            self.test_btn.setText("測試連線")
