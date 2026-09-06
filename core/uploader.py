"""把圖片上傳到 imgbb，取得 LINE 需要的公開 HTTPS URL。

Imgur 停用後改用 imgbb（api.imgbb.com）。只靠 requests，不需要 pyimgur。

LINE 的 ImageSendMessage 要求：
  * original_content_url / preview_image_url 都必須是 HTTPS
  * 原圖上限 10 MB、預覽圖上限 1 MB
所以預覽圖優先用 imgbb 回傳的 medium 尺寸，沒有才退回原圖。
"""

from __future__ import annotations

import base64
import os

import settings


class UploadError(RuntimeError):
    pass


def upload(image_path, name=None):
    """上傳單張圖片，回傳 (original_url, preview_url)。

    失敗會丟 UploadError，訊息裡帶 imgbb 的原始回應，方便判斷是 key 錯還是
    配額用完。
    """
    if not settings.IMGBB_API_KEY:
        raise UploadError(
            "IMGBB_API_KEY 未設定。請在 .env 填入，或參考 .env.example。"
        )
    if not os.path.exists(image_path):
        raise UploadError(f"找不到要上傳的檔案: {image_path}")

    import requests

    with open(image_path, "rb") as f:
        payload = base64.b64encode(f.read())

    data = {
        "key": settings.IMGBB_API_KEY,
        "image": payload,
        "name": name or os.path.splitext(os.path.basename(image_path))[0],
    }
    if settings.IMGBB_EXPIRATION:
        data["expiration"] = settings.IMGBB_EXPIRATION

    try:
        resp = requests.post(
            settings.IMGBB_UPLOAD_URL, data=data, timeout=settings.UPLOAD_TIMEOUT
        )
    except requests.RequestException as exc:
        raise UploadError(f"連線 imgbb 失敗: {exc}") from exc

    try:
        body = resp.json()
    except ValueError:
        raise UploadError(
            f"imgbb 回應不是 JSON (HTTP {resp.status_code}): {resp.text[:200]}"
        ) from None

    if not resp.ok or not body.get("success"):
        # imgbb 的錯誤訊息可能在 error.message 或 error 本身
        err = body.get("error")
        detail = err.get("message") if isinstance(err, dict) else err
        raise UploadError(f"imgbb 上傳失敗 (HTTP {resp.status_code}): {detail or body}")

    payload = body.get("data") or {}
    original = payload.get("url") or payload.get("display_url")
    if not original:
        raise UploadError(f"imgbb 回應缺少圖片網址: {body}")

    # medium 只有在原圖夠大時才會出現，正好當作 <1MB 的預覽圖
    medium = payload.get("medium") or {}
    thumb = payload.get("thumb") or {}
    preview = medium.get("url") or original or thumb.get("url")

    if not original.startswith("https://"):
        raise UploadError(f"imgbb 回傳的不是 HTTPS 網址，LINE 會拒收: {original}")

    return original, preview


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python uploader.py <圖片路徑>")
        raise SystemExit(2)
    orig, prev = upload(sys.argv[1])
    print(f"original: {orig}")
    print(f"preview : {prev}")
