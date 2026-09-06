
# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---
import win32gui
import win32ui
from ctypes import windll
from PIL import Image
import win32con
import numpy as np
import cv2
from datetime import datetime
import os
import threading
from CRACKpre import PreImg, PreImgArray
import paths
from crack_engine import preload
import time

def process_image(img_path, channel=1):
    try:
        PreImg(img_path, channel)  # 调用 PreImg 函数处理图像，并指定Channel
    except Exception as e:
        print(f"Failed to create PreImg: {e}")

def get_all_window_titles():
    titles = []

    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):  # 只列出可見窗口
            title = win32gui.GetWindowText(hwnd)
            if title:  # 過濾掉沒有標題的窗口
                titles.append(title)

    win32gui.EnumWindows(enum_handler, None)
    return titles

def screenshot_window_with_win32(window_title):
    # 查找目標視窗句柄
    hwnd = win32gui.FindWindow(None, window_title)
    if hwnd == 0:
        print(f"找不到視窗: {window_title}")
        return None

    # 確保視窗顯示
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    # 獲取視窗的邊界
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    
    # 計算視窗寬度與高度
    width = right - left
    height = bottom - top

    # 獲取視窗設備上下文
    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()

    # 建立與視窗大小相容的位圖
    saveBitmap = win32ui.CreateBitmap()
    saveBitmap.CreateCompatibleBitmap(mfcDC, width, height)
    saveDC.SelectObject(saveBitmap)

    # 從視窗設備上下文擷取像素
    windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 3)

    # 將位圖轉換為圖片
    bmpinfo = saveBitmap.GetInfo()
    bmpstr = saveBitmap.GetBitmapBits(True)

    img = Image.frombuffer(
        'RGB', 
        (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
        bmpstr, 'raw', 'BGRX', 0, 1)

    # 清理資源
    win32gui.DeleteObject(saveBitmap.GetHandle())
    saveDC.DeleteDC()
    mfcDC.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwndDC)

    return img

def Show_Windows_Tile():
    Windows_Title = get_all_window_titles()
    Windoes_Index = 0
    for i in Windows_Title:
        print(f"Index:{Windoes_Index} "+f"Title:{i}")
        Windoes_Index+=1
    Title_OutPut = Windows_Title[int(input("Typeing Index "))]
    return Title_OutPut

def Start_Processing_IMG(Window_Title,Channel=1):
    shot = screenshot_window_with_win32(Window_Title)
    if shot is None:
        return
    img_cv2 = np.array(shot)
    img_cv2_rgb = cv2.cvtColor(img_cv2, cv2.COLOR_BGR2RGB)
    os.makedirs(paths.raw_subdir("WINDOWSCATCH"), exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_path = os.path.join(paths.raw_subdir("WINDOWSCATCH"), f"captured_image_{timestamp}.jpg")
    cv2.imwrite(img_path, img_cv2_rgb)
    print(f"Image saved: {img_path}")
    # 直接把陣列送進模型，省掉再 imread 回來的那趟磁碟往返。
    PreImgArray(img_cv2_rgb, int(Channel),
                save_prefix=img_path.replace(".jpg", ""), save_always=True)
# img_cv2 = np.array(screenshot_window_with_win32("WINDOWSCATCH"))

# 如果 img 是 BGRX 顏色格式，轉換為 BGR 格式
# img_cv2_bgr = cv2.cvtColor(img_cv2, cv2.COLOR_BGRA2BGR)

# 如果你想將 BGR 轉換為 RGB 格式
# img_cv2_rgb = cv2.cvtColor(img_cv2, cv2.COLOR_BGR2RGB)



# timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
# img_path = f"raw/WINDOWSCATCH/captured_image_{timestamp}.jpg"

# # 去掉 .jpg 後綴
# img_path = img_path.replace(".jpg", "")

# # 使用多線程調用 PreImg 處理圖像，指定Channel
# thread = threading.Thread(target=process_image, args=(img_path, 1))

# thread.start()

if __name__=="__main__":
    preload()   # 背景載入模型，第一次擷取就不用等
    Channel = input("TypeIn Channel ")
    while True:
        # Start_Processing_IMG(Show_Windows_Tile(),Channel)
        Start_Processing_IMG("雷電模擬器",Channel)
        input("next Round Enter")