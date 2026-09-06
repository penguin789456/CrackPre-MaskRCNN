# CrackPre 桌面前端

把原本三支各自為政的 OpenCV HighGUI / pygame 腳本，換成一個 PySide6 桌面應用，
架構對齊 `D:\CrackPre` 的 `crackpre_app`。

## 執行

```bat
conda activate Chihlee
cd D:\CrackPreVer3.5.3
python main.py
```

開啟時會先跳出模式選擇：

| 模式 | 行為 | 適用 |
|---|---|---|
| 📁 **圖片模式** | 選圖片檔或整個資料夾，**每一張都處理，一張都不漏** | 事後檢視已拍好的照片 |
| 🎥 **連續模式** | 接 Tello / RTSP / 影片檔，**處理不完的舊影格會被丟掉** | 現場即時巡檢 |

這兩者不只是換來源，丟不丟幀的語意是相反的，所以做成啟動時詢問，而不是一個
容易誤按的開關。

無介面煙霧測試：

```bat
python main.py --selftest
```

## Tello 手動飛行操控

連續模式 + 來源選「Tello EDU」時，控制列下方會出現飛行面板。按鍵配置與舊的
`manual-control-pygame.py` 一致，飛手不用重新學：

| 鍵 | 動作 |
|---|---|
| ↑ ↓ | 前進 / 後退 |
| ← → | 左移 / 右移 |
| W / S | 上升 / 下降 |
| A / D | 左轉 / 右轉 |
| T | 起飛 |
| L | 降落 |

面板另有「連線 / 起飛 / 降落 / 緊急停止」按鈕、速度滑桿（10–100，預設 60）與
電量／高度顯示。**鍵盤要先點一下畫面取得焦點**才會生效。

「緊急停止」會切斷馬達讓機體直接墜落，所以只做成按鈕並加確認對話框，不綁快速鍵。

### 三個刻意的行為差異

**1. rc 指令頻率從 125 Hz 降到 20 Hz。**
舊版 `pygame.time.set_timer(USEREVENT, 1000 // 120)` 等於每 8 ms 送一次
`send_rc_control`。Tello SDK 建議 rc 不超過 20 Hz；灌太快會塞爆指令通道，反而
讓起飛／降落這種需要回應的指令被拖延。selftest 有斷言擋住這個值被改回去。

**2. Tello 連線由 `TelloController` 持有，`TelloBackend` 只讀影格。**
兩邊各自 `Tello()` 會搶同一個 UDP 8889，第二個必定失敗。`TelloBackend(tello=...)`
接受外部傳入的連線，且**不會**在 release() 時關掉不屬於它的連線。

**3. 停止偵測不會自動降落。** 只把速度歸零讓它懸停——在半空中自動降落可能比
懸停更危險。關閉視窗時若機體仍在飛，會跳出對話框讓你選「降落後關閉／保持懸停／
不關閉」，不會靜默地留一台沒人控制的飛機。

## 結構

對齊 `D:\CrackPre\crackpre_app`：

```
main.py                     啟動器（把 core/ 加進 sys.path）
crackpre_app/
  app.py                    QApplication、模式詢問、--selftest
  mode_dialog.py            啟動時的模式選擇對話框
  gui.py                    主視窗，控制列依模式改變
  engine.py                 偵測迴圈，產生 FrameResult / HitRecord
  sources.py                CaptureBackend：Image / Video / Tello / RTSP / Fake
  worker.py                 QThread worker，用 Qt signal 回報
  config.py                 路徑 + .env（轉發到 core/settings.py）
  upload.py                 手動上傳 imgbb -> LINE
  settings_dialog.py        金鑰設定，含實際連線測試
  tello_control.py          Tello 手動飛行操控（速度狀態機 + rc 節流）
```

推論後端沿用既有的 `core/`（`crack_engine.py`、`visualize.py`、`settings.py`、
`uploader.py`），**沒有重寫**。這裡換掉的只有前端。

## 與舊版的三個行為差異

**1. 不再自動推播 LINE。** 舊版每偵測到一次就送一則；連續模式下會以每 0.2 秒
一則的速度把 LINE 配額打爆。現在偵測只在本機存檔，要送哪張由你在右側命中清單
自己挑，按「上傳到 LINE」。

**2. 存檔位置改到 `%APPDATA%\CrackPre\hits`。** 不再寫進程式目錄，之後要打包成
唯讀安裝也不會壞。

**3. 丟幀語意依模式而定。** 連續模式用獨立讀取執行緒只保留最新一格；圖片模式
關掉這個行為，逐張處理。狀態列會顯示「幀齡」與「已丟 N 幀」——**連續模式下
已丟數持續增加是正確的**，代表延遲沒有累積。

## 驗證

```
python main.py --selftest
```

實測輸出：

```
selftest OK: 連續模式處理 1 幀、命中 1 張；圖片模式選擇器 有、處理 3/3 張（相符）；飛行操控 通過
```

「3/3 相符」是圖片模式的核心保證：選幾張就處理幾張，不會因為丟幀機制而漏掉。

飛行操控的部分**不需要真的有 Tello**：連線相關全部 mock 掉，驗的是八組按鍵到
速度欄位的對應（含放開歸零）、面板只在 Tello 來源時顯示、rc 間隔不小於 50 ms、
以及未連線時送 rc 不會爆炸。

## 未做

- **MSI 打包**（依你的要求排除）。要做的話參考 `D:\CrackPre\packaging\`。
- 舊的入口腳本 `RTSP.py` / `manual-control-pygame.py` / `WINDOWNCATCH.py` 與
  對應的 `.bat` 都**原封不動保留**，命令列量測工具（`bench_latency.py`、
  `bench_continuous.py`、`compare_legacy.py`）也還在。新舊可以並存。
- **飛行操控只在真機上驗證過按鍵對應與狀態機，沒有實際連過 Tello**
  （開發環境沒有機體）。第一次實飛請在空曠處、低速、隨時準備按 L 降落。
