
# --- bootstrap: 讓散裝的後端模組（core/）可以被 import ---
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "core"))
# --- end bootstrap ---
from djitellopy import Tello
import cv2
import pygame
import numpy as np
import time
from CRACKpre import PreImg
import paths
from crack_engine import preload
from datetime import datetime
import threading
import os


S = 60
FPS = 120

class FrontEnd(object):
    def __init__(self):
        preload()   # 背景載入模型，起飛前就準備好，按下拍照鍵不用等模型
        pygame.init()
        pygame.display.set_caption("Tello video stream")
        self.screen = pygame.display.set_mode([960, 720])
        self.tello = Tello()
        self.for_back_velocity = 0
        self.left_right_velocity = 0
        self.up_down_velocity = 0
        self.yaw_velocity = 0
        
        self.speed = 10
        self.send_rc_control = False
        self.capture_button_rect = pygame.Rect(800, 650, 140, 50)  
        pygame.time.set_timer(pygame.USEREVENT + 1, 1000 // FPS)
        
        # 確保raw/TELLO文件夾存在
        os.makedirs(paths.raw_subdir("TELLO"), exist_ok=True)
        
        # 創建按鍵到Channel的映射字典
        self.key_to_channel = {
            pygame.K_1: 1,
            pygame.K_2: 2,
            pygame.K_3: 3,
            pygame.K_4: 4,
            pygame.K_5: 5,
            pygame.K_6: 6,
            pygame.K_7: 7,
            pygame.K_8: 8,
            pygame.K_9: 9
        }

    def run(self):
        self.tello.connect()
        self.tello.set_speed(self.speed)
        self.tello.streamoff()
        self.tello.streamon()

        frame_read = self.tello.get_frame_read()
        should_stop = False
        while not should_stop:
            for event in pygame.event.get():
                if event.type == pygame.USEREVENT + 1:
                    self.update()
                elif event.type == pygame.QUIT:
                    should_stop = True
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        should_stop = True
                    else:
                        self.keydown(event.key)
                elif event.type == pygame.KEYUP:
                    self.keyup(event.key)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1 and self.capture_button_rect.collidepoint(event.pos):
                        frameImg = frame_read.frame
                        frameImg = cv2.cvtColor(frameImg, cv2.COLOR_BGR2RGB)
                        frameImg = np.rot90(frameImg)
                        frameImg = np.flipud(frameImg)
                        self.capture_image(frameImg, 1)  # 默認使用Channel 1

            if frame_read.stopped:
                break

            self.screen.fill([0, 0, 0])
            frame = frame_read.frame
            text = "Battery: {}%".format(self.tello.get_battery())
            cv2.putText(frame, text, (5, 720 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = np.rot90(frame)
            frame = np.flipud(frame)
            frame = frame[:, :, ::-1]

            frame = pygame.surfarray.make_surface(frame)
            self.screen.blit(frame, (0, 0))
            # self.draw_capture_button()  
            pygame.display.update()
            time.sleep(1 / FPS)

        self.tello.end()

    def draw_capture_button(self):
        pygame.draw.rect(self.screen, (0, 255, 0), self.capture_button_rect)  
        font = pygame.font.Font(None, 36)
        text_surface = font.render("Capture Image", True, (255, 255, 255))
        self.screen.blit(text_surface, (self.capture_button_rect.x + 10, self.capture_button_rect.y + 10))

    def capture_image(self, frame, channel=1):
        if frame is None:
            print("Failed to capture image: frame is None.")
            return
        
        timestamp = datetime.now().strftime("%y-%m-%d %H-%M-%S")
        img_path = os.path.join(paths.raw_subdir("TELLO"), f"captured_image_{timestamp}.jpg")
        frame=cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        cv2.imwrite(img_path, frame)
        img_path=img_path.replace(".jpg","")
        
        thread = threading.Thread(target=self.process_image, args=(img_path, channel))
        thread.start()
        print(f"Processing image with Channel {channel}")

    def process_image(self, img_path, channel=1):
        try:
            PreImg(img_path, channel)
        except Exception as e:
            print(f"Failed to create PreImg: {e}")

    def keydown(self, key):
        if key == pygame.K_UP:
            self.for_back_velocity = S
        elif key == pygame.K_DOWN:
            self.for_back_velocity = -S
        elif key == pygame.K_LEFT:
            self.left_right_velocity = -S
        elif key == pygame.K_RIGHT:
            self.left_right_velocity = S
        elif key == pygame.K_w:
            self.up_down_velocity = S
        elif key == pygame.K_s:
            self.up_down_velocity = -S
        elif key == pygame.K_a:
            self.yaw_velocity = -S
        elif key == pygame.K_d:
            self.yaw_velocity = S
        elif key == pygame.K_t:
            self.tello.takeoff()
            self.send_rc_control = True
        elif key == pygame.K_l:
            not self.tello.land()
            self.send_rc_control = False
        elif key == pygame.K_p:
            frame_read = self.tello.get_frame_read()
            frameImg = frame_read.frame
            frameImg = cv2.cvtColor(frameImg, cv2.COLOR_BGR2RGB)
            frameImg = np.rot90(frameImg)
            frameImg = np.flipud(frameImg)
            self.capture_image(frameImg, 1)  # 默認使用Channel 1
        elif key in self.key_to_channel:
            # 使用字典查找對應的Channel
            channel = self.key_to_channel[key]
            frame_read = self.tello.get_frame_read()
            frameImg = frame_read.frame
            frameImg = cv2.flip(frameImg, 0)
            frameImg = cv2.cvtColor(frameImg, cv2.COLOR_BGR2RGB)
            frameImg = np.rot90(frameImg)
            frameImg = np.flip(frameImg, axis=(0, 1))
            frameImg = np.flip(frameImg, axis=0)
            # frameImg = np.flipud(frameImg)
            self.capture_image(frameImg, channel)  # 使用對應的Channel

    def keyup(self, key):
        if key == pygame.K_UP or key == pygame.K_DOWN:
            self.for_back_velocity = 0
        elif key == pygame.K_LEFT or key == pygame.K_RIGHT:
            self.left_right_velocity = 0
        elif key == pygame.K_w or key == pygame.K_s:
            self.up_down_velocity = 0
        elif key == pygame.K_a or key == pygame.K_d:
            self.yaw_velocity = 0

    def update(self):
        if self.send_rc_control:
            self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity,
                self.up_down_velocity, self.yaw_velocity)

def main():
    frontend = FrontEnd()
    frontend.run()

if __name__ == '__main__':
    main()
