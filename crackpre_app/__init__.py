"""CrackPre 桌面前端。

架構對齊 D:\\CrackPre 的 crackpre_app：app / gui / engine / sources / worker /
config / upload / settings_dialog。差別在推論後端——這裡沿用本專案既有的
Mask R-CNN 引擎（CrackPre/crack_engine.py），不是 DDRNet。

啟動時會先問要用「圖片模式」還是「連續模式」，見 mode_dialog.py。
"""

__version__ = "3.5.3"
