"""裂縫偵測的對外介面。

介面與舊版相容：RTSP.py / manual-control-pygame.py / WINDOWNCATCH.py 呼叫的
``PreImg(img_path_without_extension, channel)`` 行為不變。

差別在實作：
  * 模型改由 crack_engine 的單例提供，不再每張圖重建一次 predictor。
  * 畫框改用 visualize（OpenCV），不再需要 matplotlib。
  * 圖片上傳（imgbb）與 LINE 推播改成背景佇列，不再阻塞推論。
  * 金鑰改由 .env 提供，不再散落在 config.ini 與程式碼裡。

連續判別請改用 ``PreImgArray(bgr, channel)``，可以省掉 imwrite/imread 這趟
磁碟往返；只有在真的偵測到裂縫時才落地存檔。
"""

from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import cv2

import notifier
import settings
import uploader
from crack_engine import CLASS_NAMES, get_engine, preload  # noqa: F401 - preload 對外公開
from visualize import draw_detections

# 高於這個信心度才送通知，維持舊版行為。
ALERT_THRESH = float(os.getenv("CRACK_ALERT_THRESH", "0.8"))

# 給舊程式碼參考用，維持既有名稱。
LINE_CHANNELS = notifier.LINE_CHANNELS
LINE_CHANNEL_ACCESS_TOKEN = settings.LINE_CHANNEL_ACCESS_TOKEN


def PreImgArray(image, channel=1, save_prefix=None, save_always=False):
    """對記憶體中的 BGR 影像做偵測。

    :param image: BGR ndarray。
    :param channel: LINE channel 索引。
    :param save_prefix: 不含副檔名的輸出路徑；None 表示自動產生於 raw/。
    :param save_always: True 時不論信心度都存檔（舊版 PreImg 的行為）。
    :returns: (annotated_bgr, Detections)
    """
    engine = get_engine()
    det = engine.infer(image)

    if len(det) == 0:
        print(f"未偵測到任何裂縫（{det.latency_ms:.0f} ms）")
        annotated = image
    else:
        annotated = draw_detections(image, det)
        best = det.best_score
        best_class = CLASS_NAMES[int(det.classes[int(det.scores.argmax())])]
        print(
            f"偵測到 {len(det)} 處，最高信心 {best * 100:.0f}% ({best_class})"
            f"，{det.latency_ms:.0f} ms"
        )

    should_alert = len(det) > 0 and det.best_score > ALERT_THRESH
    if should_alert or save_always:
        if save_prefix is None:
            # 用絕對路徑，避免結果隨呼叫端的工作目錄跑掉
            fallback_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "raw")
            os.makedirs(fallback_dir, exist_ok=True)
            save_prefix = os.path.join(
                fallback_dir,
                f"detected_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}")
        out_path = save_prefix + "pre.jpg"
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        cv2.imwrite(out_path, annotated)
        print(f"{out_path} is written")

        if should_alert:
            msg = "The crack model confidence is over 80% in {0}%".format(
                int(det.best_score * 100)
            )
            notifier.notify(out_path, msg, channel)

    return annotated, det


def PreImg(img, channel=1):
    """舊版介面：``img`` 是不含 ``.jpg`` 的檔名前綴。"""
    path = img + ".jpg"
    if not os.path.exists(path):
        print(f"Error: Image file {path} does not exist")
        return None
    image = cv2.imread(path)
    if image is None:
        print(f"Error: Failed to read image {path}")
        return None
    # save_always=True 以維持舊版「一定產生 *pre.jpg」的行為。
    annotated, _ = PreImgArray(image, channel, save_prefix=img, save_always=True)
    return annotated


# --------------------------------------------------------------------------
# 相容用的薄包裝
# --------------------------------------------------------------------------


def LineSend(imgPath, message, channel=1):
    """保留舊名稱；現在是非阻塞的（imgPath 為本機路徑）。"""
    notifier.notify(imgPath, message, channel)


def uploadImage(ImgPath):
    """上傳圖片並回傳公開網址（原圖）。"""
    original, _preview = uploader.upload(ImgPath)
    return original


def imgurUpload(ImgPath):
    """舊名稱保留。Imgur 已停用，實際走的是 imgbb。"""
    return uploadImage(ImgPath)


def SendEmail(sender_email, receiver_email, imgPath):
    msg = MIMEMultipart()
    with open(imgPath, "rb") as file:
        img = file.read()
    attach_file = MIMEApplication(img, Name="meme.jpg")
    attach_file["Content-Disposition"] = f'attachment; filename="{imgPath}"'
    msg.attach(attach_file)
    msg.attach(MIMEText("Crack predict possible is higer %", "plain", "utf-8"))

    msg["Subject"] = "Crack predict IMG 80% Upper"
    msg["From"] = sender_email
    msg["To"] = receiver_email

    smtp = smtplib.SMTP("smtp.gmail.com", 587)
    smtp.ehlo()
    smtp.starttls()
    smtp.login(sender_email, os.getenv("GMAIL_APP_PASSWORD"))
    smtp.send_message(msg)
    smtp.quit()
