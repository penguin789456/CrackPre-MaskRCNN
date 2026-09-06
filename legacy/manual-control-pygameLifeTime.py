
# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---
from djitellopy import Tello
import cv2
import numpy as np
import time
from CRACKpreLifeTime import PreImg
from datetime import datetime
import threading
import os

S = 60
FPS = 120

class FrontEnd(object):
    def __init__(self):
        self.tello = Tello()
        self.for_back_velocity = 0
        self.left_right_velocity = 0
        self.up_down_velocity = 0
        self.yaw_velocity = 0
        self.speed = 10
        self.send_rc_control = False
        
        # 確保raw/TELLO文件夾存在
        os.makedirs(paths.raw_subdir("TELLO"), exist_ok=True)
        
        # 創建按鍵到Channel的映射字典
        self.key_to_channel = {
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

    def run(self):
        self.tello.connect()
        self.tello.set_speed(self.speed)
        self.tello.streamoff()
        self.tello.streamon()

        frame_read = self.tello.get_frame_read()
        should_stop = False
        while not should_stop:
            if frame_read.stopped:
                break

            frame = frame_read.frame
            if frame is None:
                continue

            # Process frame in real-time
            processed_frame = self.process_frame(frame)
            
            # # Display battery info on processed frame
            # text = "Battery: {}%".format(self.tello.get_battery())
            # cv2.putText(processed_frame, text, (5, 720 - 5),
            #             cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            
            # Show processed frame
            cv2.imshow("Tello Video Stream with Crack Detection", processed_frame)
            
            # Handle keyboard input
            key = cv2.waitKey(1) & 0xff
            if key == 27:  # ESC
                should_stop = True
            elif key == ord('t'):  # Takeoff
                self.tello.takeoff()
                self.send_rc_control = True
            elif key == ord('l'):  # Land
                self.tello.land()
                self.send_rc_control = False
            elif key == ord('w'):  # Up
                self.up_down_velocity = S
            elif key == ord('s'):  # Down
                self.up_down_velocity = -S
            elif key == ord('a'):  # Yaw left
                self.yaw_velocity = -S
            elif key == ord('d'):  # Yaw right
                self.yaw_velocity = S
            elif key == ord('i'):  # Forward
                self.for_back_velocity = S
            elif key == ord('k'):  # Backward
                self.for_back_velocity = -S
            elif key == ord('j'):  # Left
                self.left_right_velocity = -S
            elif key == ord('l'):  # Right
                self.left_right_velocity = S
            elif key == ord('q'):  # Stop all movement
                self.for_back_velocity = 0
                self.left_right_velocity = 0
                self.up_down_velocity = 0
                self.yaw_velocity = 0

            # Update RC control
            if self.send_rc_control:
                self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity,
                    self.up_down_velocity, self.yaw_velocity)

            time.sleep(1 / FPS)

        cv2.destroyAllWindows()
        self.tello.end()

    def process_frame(self, frame):
        try:
            # Process the frame using PreImg
            processed_frame = PreImg(frame)
            return processed_frame
        except Exception as e:
            print(f"Failed to process frame: {e}")
            return frame  # Return original frame if processing fails

def main():
    frontend = FrontEnd()
    frontend.run()

if __name__ == '__main__':
    main()
