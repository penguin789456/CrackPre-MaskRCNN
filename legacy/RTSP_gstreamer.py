
# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GObject
import cv2
import numpy as np
from datetime import datetime
import threading
import paths
from CRACKpre import PreImg
import os

# 初始化 GStreamer
Gst.init(None)

# RTSP URL
rtsp_url = "rtsp://192.168.144.25:8554/main.264"

# 確保raw/RTSP文件夾存在
os.makedirs(paths.raw_subdir("RTSP"), exist_ok=True)

# 創建按鍵到Channel的映射字典
key_to_channel = {
    ord('1'): 1,
    ord('2'): 2,
    ord('3'): 3,
    ord('4'): 4,
    ord('5'): 5,
    ord('6'): 6,
    ord('7'): 7,
    ord('8'): 8,
    ord('9'): 9
}

# 建立 GStreamer pipeline（指定 BGR，使用TCP連接）
pipeline = Gst.parse_launch(
    f'rtspsrc location={rtsp_url} latency=10 protocols=tcp ! '
    'rtph265depay ! avdec_h265 ! '
    'videoconvert ! video/x-raw,format=BGR ! '
    'appsink name=sink emit-signals=True drop=True max-buffers=1'
)

# 取得 appsink
appsink = pipeline.get_by_name('sink')

# 定義幀處理 callback
def on_new_buffer(appsink):
    sample = appsink.emit('pull-sample')
    if sample:
        buffer = sample.get_buffer()
        success, map_info = buffer.map(Gst.MapFlags.READ)
        if success:
            try:
                # 解析解析度
                caps = sample.get_caps()
                width = caps.get_structure(0).get_value('width')
                height = caps.get_structure(0).get_value('height')

                # 直接使用 BGR 格式
                frame = np.ndarray(
                    shape=(height, width, 3),  # BGR 是 3 通道
                    dtype=np.uint8,
                    buffer=map_info.data
                )

                # 顯示畫面
                cv2.imshow("RTSP Stream (BGR)", frame)
                
                # 檢查按鍵並處理
                key = cv2.waitKey(1)
                if key in key_to_channel:
                    # 獲取對應的Channel
                    channel = key_to_channel[key]
                    
                    # 保存當前幀並發送到指定的Channel
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    img_path = os.path.join(paths.raw_subdir("RTSP"), f"captured_image_{timestamp}.jpg")
                    cv2.imwrite(img_path, frame)
                    print(f"Image saved: {img_path}")

                    # 去掉 .jpg 後綴
                    img_path = img_path.replace(".jpg", "")

                    # 使用多線程調用 PreImg 處理圖像，指定Channel
                    thread = threading.Thread(target=process_image, args=(img_path, channel))
                    thread.start()
                    print(f"Processing image with Channel {channel}")

                # 按下 'q' 鍵退出
                if key & 0xFF == ord('q'):
                    loop.quit()

            finally:
                buffer.unmap(map_info)
        return Gst.FlowReturn.OK
    return Gst.FlowReturn.ERROR

# 图像处理函数
def process_image(img_path, channel=1):
    try:
        PreImg(img_path, channel)  # 调用 PreImg 函数处理图像，并指定Channel
    except Exception as e:
        print(f"Failed to create PreImg: {e}")

# 綁定 callback
appsink.connect('new-sample', on_new_buffer)

# 播放 pipeline
pipeline.set_state(Gst.State.PLAYING)

# 事件循環
loop = GObject.MainLoop()

try:
    loop.run()
except KeyboardInterrupt:
    print("Stopped by user")

# 清理
pipeline.set_state(Gst.State.NULL)
cv2.destroyAllWindows()