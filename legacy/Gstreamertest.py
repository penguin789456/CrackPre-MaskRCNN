import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GObject
import numpy as np
import cv2

# 初始化 GStreamer
Gst.init(None)

# RTSP URL
rtsp_url = 'rtsp://192.168.144.25:8554/main.264'

# 建立 GStreamer pipeline（指定 BGR）
pipeline = Gst.parse_launch(
    f'rtspsrc location={rtsp_url} latency=10 ! '
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
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    loop.quit()

            finally:
                buffer.unmap(map_info)
        return Gst.FlowReturn.OK
    return Gst.FlowReturn.ERROR

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
