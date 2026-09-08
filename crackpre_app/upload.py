"""手動把命中結果上傳到 LINE。

沿用既有的 CrackPre/uploader.py（imgbb）。與舊版最大的差別：**偵測迴圈不再
自動推播**。舊版每偵測到一次就送一則，連續模式下會以每 0.2 秒一則的速度把
LINE 配額打爆。現在改成使用者在命中清單裡自己挑要送哪張。

這裡保持與 GUI 無關；執行緒包裝在 gui.py。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, QThread, Signal, Slot


def upload_hit_to_line(path: str, message: str, channel: int = 1) -> str:
    """上傳一張圖並推播到指定 Channel，回傳圖片公開網址。失敗會丟例外。"""
    import settings
    import uploader

    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到檔案: {path}")

    token = settings.LINE_CHANNEL_ACCESS_TOKEN
    if not token:
        raise RuntimeError("尚未設定 LINE_CHANNEL_ACCESS_TOKEN，請開啟「設定」填入。")

    channels = settings.load_channels()
    if not (0 <= channel < len(channels)) or not channels[channel]:
        available = [i for i, c in enumerate(channels) if c]
        raise RuntimeError(
            f"Channel {channel} 未設定。目前可用的是 {available or '（無）'}。")
    target = channels[channel]

    original, preview = uploader.upload(path)

    from linebot import LineBotApi
    from linebot.models import ImageSendMessage, TextSendMessage

    api = LineBotApi(token)
    api.push_message(target, TextSendMessage(text=message))
    api.push_message(
        target,
        ImageSendMessage(original_content_url=original, preview_image_url=preview),
    )
    return original


class UploadTask(QObject):
    """在背景執行緒跑上傳，避免上傳期間 UI 凍住（逾時預設 30 秒）。"""

    done = Signal(str)     # 圖片網址
    failed = Signal(str)

    def __init__(self, path: str, message: str, channel: int) -> None:
        super().__init__()
        self._args = (path, message, channel)

    @Slot()
    def run(self) -> None:
        try:
            self.done.emit(upload_hit_to_line(*self._args))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def start_upload(path, message, channel, on_done, on_failed, parent=None):
    """啟動一次背景上傳。回傳 (thread, task)，呼叫端要保留參考避免被 GC。

    ``on_done`` / ``on_failed`` **必須是某個主執行緒 QObject 的 bound method**，
    不能傳普通函式或 lambda——否則 Qt 判斷不出接收端的執行緒歸屬，會用
    DirectConnection 在 worker 執行緒裡執行回呼，一碰 GUI 就出事。
    """
    thread = QThread(parent)
    task = UploadTask(path, message, channel)
    task.moveToThread(thread)
    thread.started.connect(task.run)
    task.done.connect(on_done)
    task.failed.connect(on_failed)
    task.done.connect(thread.quit)
    task.failed.connect(thread.quit)
    thread.start()
    return thread, task
