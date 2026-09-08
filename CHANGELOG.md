# 更新紀錄

## 3.6.0

PySide6 桌面前端，取代原本三支各自為政的 OpenCV HighGUI / pygame 腳本。
舊腳本仍保留在 `legacy/`，可獨立執行。

### 新增

- **桌面應用** `python main.py`。啟動時選「圖片模式」或「連續模式」——
  兩者丟不丟影格的語意相反，所以做成明確選擇而不是開關。
- **預覽／辨識分離**。連續模式用下拉選行為，搭配單一「連線」鈕。
  只預覽不載模型，實測 22 fps；辨識模式跟著推論速度走（CPU 約 0.3 fps）。
- **Tello 手動飛行操控**。按鍵沿用舊 `manual-control-pygame.py`
  （↑↓←→ / W S / A D / T / L）。rc 指令頻率降到 20 Hz，符合 Tello SDK 建議。
- **拍一張（`P`）**。辨識模式立刻存已分析的畫面；預覽模式則單獨分析那一格
  再存。不看門檻也不受冷卻限制。
- **啟動即載入模型**，畫面下方顯示載入狀態與實際使用的後端。
- **設定可選推論引擎**（auto / onnx / torchscript / detectron2）。
  不可用的選項會列出並標明原因，而不是藏起來。
- 專案結構重整：`core/` `crackpre_app/` `tools/` `legacy/` `models/`
  `data/` `docs/` `requirements/` `third_party/` `bin/`。

### 修正

- **上傳 LINE 後整個程式卡住**。背景工作的回呼接的是區域 closure，PySide
  判斷不出接收端執行緒歸屬而退回 DirectConnection，導致 `QMessageBox` 在
  worker 執行緒被建立（`QObject::setParent` / `QBasicTimer::stop` 錯誤）。
  回呼改成主執行緒 QObject 的 bound method。預覽模式按 `P` 有同樣的問題，
  一併修正。
- **`T` 快速鍵會繞過起飛前置檢查**。`TelloController` 不再處理 `T`/`L`，
  改由 GUI 走與按鈕相同的路徑。
- **Tello 連線擁有權**。改由 `TelloController` 統一持有；先前 `TelloBackend`
  可能自行開一條連線，讓飛行指令靜默失效並搶占 UDP 8889。
- 修好文件裡被轉義吃掉的路徑反斜線。

### 行為變更

- **偵測過程不再自動推播 LINE**。改由使用者在命中清單手動選擇上傳。
  舊版連續模式會以每 0.2 秒一則的速度打爆 LINE 配額。
- **起飛前必須先開啟影像串流**。未開啟時顯示提示並中斷起飛，不會自動代開——
  起飛是不可逆的實體動作，不該是別的操作的副作用。
- 命中結果存到 `%APPDATA%\CrackPre\hits`，不再寫進程式目錄。

### 尚未驗證

- **飛行操控未在真機測試過**（開發環境沒有 Tello）。按鍵對應、狀態機與
  連線共用邏輯有測試涵蓋，真機的起飛／降落／rc 響應尚未實測。
- **ONNX 後端未做精度回歸**。TorchScript 已對 80 張真實影像逐位元比對通過。

---

## 3.5.3

依賴瘦身與延遲最佳化。詳見 [docs/OPTIMIZATION.md](docs/OPTIMIZATION.md)。

- 模型匯出為 TorchScript / ONNX，執行期不再需要 detectron2、pycocotools
  與 C++ Build Tools。
- RTSP 改用 OpenCV 的 FFmpeg backend，移除整串 GStreamer / PyGObject 依賴。
- 推論引擎改為 process 內單例並加上 warmup；舊版每張圖都重建 predictor 並
  重新載入 169 MB 權重。
- 連續判別改為「最新幀優先」，延遲不再累積。
- 金鑰全部移到 `.env`；Imgur API 停用，改用 ImgBB。
