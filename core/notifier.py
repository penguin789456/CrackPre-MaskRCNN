"""背景通知佇列：圖片上傳（imgbb）+ LINE 推播。

原本 PreImg 在推論完之後，直接在同一個執行緒裡同步做圖片上傳和兩次
LINE push_message。那是三趟網際網路 round-trip，動輒 1~3 秒，而且是接在
推論後面算進使用者感受到的延遲裡。連續判別時更糟：上一張還在上傳，下一張
就進不來。

這裡改成單一背景 worker + 有界佇列。推論執行緒只做 enqueue（微秒等級）。
佇列滿的時候丟掉最舊的一筆，寧可少送一則通知，也不要把偵測管線塞住。
"""

from __future__ import annotations

import os
import queue
import threading

import settings
import uploader

MAX_PENDING = int(os.getenv("CRACK_NOTIFY_QUEUE", "8"))

LINE_CHANNEL_ACCESS_TOKEN = settings.LINE_CHANNEL_ACCESS_TOKEN
LINE_CHANNELS = settings.load_channels()

_queue = queue.Queue(maxsize=MAX_PENDING)
_worker = None
_worker_lock = threading.Lock()
_line_api = None


def _get_line_api():
    """LineBotApi 內含一個 requests.Session，重複使用可以省掉每次的 TLS handshake。"""
    global _line_api
    if _line_api is None:
        from linebot import LineBotApi

        _line_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
    return _line_api


def _resolve_channel(channel):
    if 0 <= channel < len(LINE_CHANNELS) and LINE_CHANNELS[channel] is not None:
        return LINE_CHANNELS[channel]
    print(f"[notifier] 無效的 Channel 索引 {channel}，改用預設 Channel")
    return LINE_CHANNELS[0] if LINE_CHANNELS else ""


def _deliver(img_path, message, channel):
    from linebot.models import ImageSendMessage, TextSendMessage

    target = _resolve_channel(channel)
    if not target:
        print("[notifier] 沒有可用的 Channel ID，略過推播")
        return

    original, preview = uploader.upload(img_path)
    api = _get_line_api()
    api.push_message(target, TextSendMessage(text=message))
    api.push_message(
        target,
        ImageSendMessage(original_content_url=original, preview_image_url=preview),
    )
    print(f"[notifier] 已送出到 Channel {channel}")


def _run():
    while True:
        job = _queue.get()
        if job is None:
            _queue.task_done()
            return
        img_path, message, channel = job
        try:
            _deliver(img_path, message, channel)
        except Exception as exc:  # noqa: BLE001 - 通知失敗不該影響偵測
            print(f"[notifier] 傳送失敗到 Channel {channel}: {exc}")
        finally:
            _queue.task_done()


def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        with _worker_lock:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_run, name="crack-notifier", daemon=True)
                _worker.start()
    return _worker


def notify(img_path, message, channel=1):
    """排入通知，立刻返回。佇列滿時丟掉最舊的一筆。"""
    _ensure_worker()
    try:
        _queue.put_nowait((img_path, message, channel))
    except queue.Full:
        try:
            dropped = _queue.get_nowait()
            _queue.task_done()
            print(f"[notifier] 佇列已滿，丟棄較舊的通知: {dropped[0]}")
        except queue.Empty:
            pass
        try:
            _queue.put_nowait((img_path, message, channel))
        except queue.Full:
            print("[notifier] 佇列仍滿，放棄此則通知")


def flush(timeout=30):
    """等待佇列清空，收工前呼叫。"""
    if _worker is None or not _worker.is_alive():
        return
    done = threading.Event()

    def _wait():
        _queue.join()
        done.set()

    threading.Thread(target=_wait, daemon=True).start()
    done.wait(timeout)
