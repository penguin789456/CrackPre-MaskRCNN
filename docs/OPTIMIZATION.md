# 依賴瘦身與延遲最佳化

針對兩個問題的處理紀錄：**(1) 模型依賴過於龐大、環境建置麻煩**，
**(2) 連續圖像判別延遲過久**。

---

## 問題 2：連續判別延遲

### 根因

舊的 `CRACKpre.py:59`：

```python
def PreImg(img, channel=1):
    cfg = config.get_cfg()
    ...
    predictor = DefaultPredictor(cfg)   # <-- 每張圖都重來一次
    outputs = predictor(image)
```

`DefaultPredictor(cfg)` 會重新 build 一次 Mask R-CNN R50-FPN 的計算圖、
從磁碟讀進 **176 MB** 的 `model_final.pth`、再搬到 GPU。這件事跟「判別一張圖」
無關，卻被放在每一次呼叫裡。實際推論本身只佔整體時間的個位數百分比。

延遲來源由大到小：

| 來源 | 每張成本 | 處理方式 |
|---|---|---|
| 每張重建 predictor + 載入 176 MB 權重 | 秒級 | 改為 process 內單例，只做一次 |
| 第一次推論的 CUDA context / cuDNN autotune | 秒級（僅首次） | 啟動時 warmup 吃掉 |
| `detectron2` Visualizer + matplotlib 畫圖 | 100–300 ms | 改用 OpenCV 畫，約 2–5 ms |
| imgur 上傳 + 兩次 LINE push（同步） | 1–3 s | 移到背景佇列，推論不等它 |
| `imwrite` 後再 `imread` 讀回來 | 數十 ms | 新增 `PreImgArray()` 直接吃陣列 |
| 每次拍照開一條新執行緒各自建模型 | GPU OOM 風險 | 單槽 worker，只保留最新一幀 |

### 改動

- `crack_engine.py` — 執行緒安全的單例引擎，含 warmup。
- `visualize.py` — 用 OpenCV 取代 `detectron2.utils.visualizer` + matplotlib。
- `notifier.py` — imgur/LINE 走背景 worker + 有界佇列（滿了丟最舊的，不阻塞）。
- `pipeline.py` — `LatestFrameWorker`：連續模式下新幀覆蓋舊幀，延遲不會越積越大。
- `CRACKpre.py` — 保留舊介面 `PreImg(prefix, channel)`，新增 `PreImgArray(bgr, channel)`。

### 量測

```
cd D:\CrackPreVer3.5.3
python tools\bench_latency.py --legacy      # 新舊行為並排比較
```

看 **p50 / p95**，不要只看平均——平均會被第一次的模型載入洗掉。

實測（同上環境，**CPU-only**）：

```
單例 / detectron2       : p50 3358.8 ms
舊版 / 每張重建 predictor : p50 4170.8 ms
```

省下約 810 ms/張。但這台機器沒有 CUDA，推論本身就吃掉 3.3 秒，把比例壓平了。
**有 GPU 的機器上這個比例會完全不同**：推論會掉到百毫秒級，而每張重建的成本
（建圖 + 讀 176 MB + 搬上 GPU）不會變小反而更貴。請在實際部署的機器上重跑
這一項才是有意義的數字。

另外，`compare_legacy.py` 量到的 `舊 3034 ms -> 新 3000 ms` **不是**單例化的效果
（那支腳本兩邊都只建一次模型），而是 detectron2 eager vs TorchScript 的差距——
在 CPU 上兩者幾乎一樣。**匯出的價值是砍依賴，不是加速**，不要混為一談。

還要更快的話（依效果排序）：

```
set CRACK_MIN_SIZE=640          # 預設 800，降低輸入解析度，近乎線性換延遲
set CRACK_MAX_SIZE=1024         # 預設 1333
set CRACK_BACKEND=onnx          # 通常比 detectron2 路徑快
set CRACK_CONTINUOUS_INTERVAL=0.3
```

代價是小裂縫召回率會下降，調完請用實際資料確認。

---

## 問題 1：依賴龐大

### 根因

重量級依賴其實分成兩群，而部署端只需要「推論」：

1. **detectron2 + torch + torchvision + pycocotools + fvcore + hydra + omegaconf +
   yacs + matplotlib**，且 detectron2 與 pycocotools 都要現場編譯 →
   必須先裝 Visual C++ Build Tools。這是 `setUp.txt` 最容易卡住的一步。
2. **GStreamer 整串**：`gstreamer`、`gst-plugins-{base,good,bad,ugly}`、`gst-libav`、
   `pygobject`、`pycairo`、`glib`、`pango`、`harfbuzz`、`librsvg`、`cairo`…
   ——`ComplateENV.yaml` 裡有一半的 conda 套件是為了 `RTSP.py` 的 `import gi`。

### 改動

**(a) 模型：一次性匯出，執行期不再需要 detectron2**

```
:: 只在「匯出端」做一次（原本那個重環境）
cd D:\CrackPreVer3.5.3
python tools\export_slim_model.py --format both
```

產生 `models/model.onnx`、`models/model.ts`、`models/model_manifest.json`。
匯出腳本會拿同一張圖比對 detectron2 與匯出模型的 box / score，
誤差超標就直接報錯——**不要拿沒驗證過的模型上線**。

`crack_engine.py` 自動挑後端：

```
onnx        -> onnxruntime               不需要 torch / detectron2 / 編譯器
torchscript -> torch                     不需要 detectron2 / 編譯器
detectron2  -> torch + detectron2        fallback，維持原路徑
```

detectron2 的前處理（`ResizeShortestEdge`）與後處理（box 還原、28×28 ROI mask
貼回原圖）已用純 numpy/OpenCV 重寫在 `crack_engine.py`，所以前兩個後端完全
不 import detectron2。

**(b) RTSP：拿掉 GStreamer**

`RTSP.py` 改用 OpenCV 內建的 FFmpeg backend（`cv2.CAP_FFMPEG`），
`opencv-python` 的 wheel 已經帶 H.265 解碼。整串 conda gst/glib/pygobject
依賴可以移除。舊版保留在 `RTSP_gstreamer.py`。

順帶修掉兩個問題：`imshow`/`waitKey` 回到主執行緒（舊版在 GStreamer 的
appsink callback 裡呼叫 HighGUI，Windows 上會偶發卡死）；抓幀執行緒只留最新
一幀，避免 FFmpeg buffer 越積越多、畫面離現實越來越遠。

### 結果

| | 舊環境 | 新部署環境 |
|---|---|---|
| 安裝方式 | conda + `pip install -e detectron2 --no-build-isolation` | `pip install -r requirements\requirements-runtime.txt` |
| C++ Build Tools | 必要 | 不需要 |
| conda | 必要 | 不需要（venv 即可） |
| 主要套件 | torch, torchvision, detectron2, pycocotools, fvcore, matplotlib, tensorboard, PyGObject, GStreamer ×10 | onnxruntime, numpy, opencv-python |

安裝步驟見 `requirements\setUp-slim.txt`；匯出端仍用原本的 `requirements\setUp.txt` + `requirements\requirements-export.txt`。

---

## 驗證

三層，由快到慢：

```
cd D:\CrackPreVer3.5.3
python tools\selftest.py        # 秒級。不需要模型權重，只要 numpy + opencv
python tools	est_parity.py     # 秒級。需要 detectron2。逐函式比對數值
python toolsmpare_legacy.py --backend torchscript --save-diff   # ~10 分鐘。端到端
```

- **`selftest.py`** — 涵蓋前後處理、畫圖、單槽 worker、設定讀取，以及用假後端跑
  完整條 `PreImgArray` / `PreImg`（含通知門檻與存檔行為）。
- **`test_parity.py`** — 把 `crack_engine` 的 numpy 實作跟 detectron2 的 torch 原版
  擺在一起逐函式比。端到端測試只會告訴你「有差」，這支會告訴你「差在哪一段」。
- **`compare_legacy.py`** — 拿 `raw/` 底下的真實影像跑舊路徑 vs 新後端。
  `--save-diff` 會把超標的影像並排存到 `data/raw/_diff/`。
- **`bench_continuous.py`** — 量連續判別，不需要無人機。見下節。

### 連續判別怎麼測

單張延遲（`bench_latency.py`）量不到連續模式真正的問題。連續模式的問題是
**延遲會不會越積越多**：舊版每按一次就開一條執行緒，前一張還沒好、下一張就
排進去，你看到的結果離現場畫面越來越遠。

關鍵指標是**幀齡**（frame age）——影格送出到 worker 真的開始處理它之間的時間。

```
python tools\bench_continuous.py --fake --compare    # 秒級，不載模型，並排對照
python tools\bench_continuous.py --seconds 30        # 真模型
```

實測（假推論 300 ms/張、送圖 15 fps）：

| | 最新幀優先 | 全部排隊（舊行為） |
|---|---|---|
| 幀齡 p50 | **12.2 ms** | **10302.4 ms** |
| 幀齡 / 推論時間 | 0.04x | 34.30x |
| 判定 | 幀齡持平 | 延遲正在累積 |

排隊模式「處理張數」比較多，但那不是優點——它只是把每一幀都算完，代價是每個
結果都對應到十秒前的畫面。連續巡檢要的是「現在牆上有沒有裂縫」，不是三十秒前。

判定用的是**幀齡中位數 / 推論時間中位數**的比值，不是前後半段的平均差。
真的在排隊時每幀都得等前面所有幀算完，幀齡必然是推論時間的數倍；沒排隊時
worker 幾乎立刻接手，幀齡遠小於推論時間。第一版用平均差，在 CPU 上只跑出
7 個樣本時被單一離群值帶偏而誤判過一次——比值不受樣本數與離群值影響。

### 沒有無人機也能跑 RTSP.py

```
python tools\make_test_video.py                  # 用 raw/ 的圖做一支循環影片
set CRACK_RTSP_URL=raw\_test_source.mp4
python RTSP.py                             # 按 c 開連續判別
```

`RTSP.py` 偵測到來源是本機檔案就會循環播放，並照影片本身的 fps 節流
（否則會用 CPU 全速把影片跑完）。

### 實測結果（2026-09-06，Chihlee env / torch 2.4.1+cpu / detectron2 0.6）

TorchScript 後端，80 張真實影像（RTSP 720p、TELLO、WINDOWSCATCH 三種解析度）：

```
最大 score 差 : 0.00000   最大 box 差 : 0.000 px
最小 mask IoU : 1.0000    偵測數不符 : 0 張
判定：通過。
```

逐位元相同。這同時回答了 tracing 泛化的疑慮——匯出時噴的那堆 `TracerWarning`
對這個模型與用法而言是良性的，在 720p 上 trace 的圖換到其他解析度仍然正確。

### 這些測試抓到的真實缺陷

第一版的 numpy 前後處理有三個錯，都是端到端回歸抓出來的，值得記錄：

1. **Resize 用了 `cv2.INTER_LINEAR`**。detectron2 對 uint8 走的是 PIL 的
   `Image.BILINEAR`（帶 support scaling 的重採樣濾波），兩者不同。送進網路的
   張量從一開始就不一樣，**score 本身**漂 0.0157~0.0448，連帶讓偵測數
   （4→5、3→4、5→4）與類別對不上。已改用 PIL。
2. **漏掉 `nonempty()` 過濾**。`detector_postprocess` 的順序是
   scale → clip → **濾掉退化 box** → 貼 mask，中間那步漏了。已補上。
3. **Mask 貼回的對齊方式錯**。detectron2 用 `grid_sample(align_corners=False)`，
   以像素中心 (+0.5) 對應到**浮點數** box 的正規化座標；原本是 crop 成整數框再
   `cv2.resize`。裂縫是細長形狀，對齊差一點 IoU 就掉到 0.847~0.922。
   已用 numpy 複製 grid_sample 的取樣邏輯。

教訓：單張圖、單一解析度的「誤差 0.0000」證明不了任何事——那張圖正好就是
tracing 的樣本。要跨解析度、跨數十張才有意義。

---

---

## 金鑰管理與圖片託管（Ver3.5.3）

### 問題

金鑰散在三個地方，全部明文：

| 位置 | 內容 |
|---|---|
| `config.ini` | LINE `CHANNEL_ACCESS_TOKEN`、imgur `CLIENT_ID`、三組 Channel ID |
| `LineBot.py:28` | LINE channel secret **直接寫死在程式碼裡** |
| `os.getenv` | `GMAIL_APP_PASSWORD`（只有這個做對了） |

同時 Imgur API 已被停用，`pyimgur` 那條路整條不通。

### 改動

- **`settings.py`** — 統一從 `.env` 讀。自帶 30 行 parser，**不需要 python-dotenv**
  （這個專案的痛點就是裝套件麻煩，不值得再多一個依賴）。支援引號、`export`
  前綴、行內註解。查找順序 專案根 `.env` → `core/.env` → 系統環境變數，
  真實環境變數優先，方便臨時覆寫。`.env` 缺項時會退回讀 `config.ini` 過渡。
- **`uploader.py`** — imgbb 取代 imgur，只用 `requests`。預覽圖優先取 imgbb 的
  `medium` 尺寸，因為 LINE 的 `preview_image_url` 有 1 MB 上限。
  同時擋掉非 HTTPS 網址（LINE 會直接拒收）。
- **`migrate_to_env.py`** — 一次性把 `config.ini` 搬進 `.env`，會備份舊檔、
  保留 `.env` 裡已填好的值。
- **`.env.example`** + **`.gitignore`** — 範本進版控，真正的 `.env` 不進。
- `config.ini` 清空機密，只留指向 `.env` 的說明。
- `LineBot.py` 啟動時檢查 token/secret，缺了就直接報錯，不會跑到驗簽才炸。

### 驗證

```
python core\settings.py        # 印出設定摘要，金鑰遮蔽
python tools\selftest.py        # 含 .env parser、settings、uploader 前置檢查
```

`uploader` 的請求格式已對真實 imgbb API 驗證過：用假 key + 合成 1×1 圖片
（刻意不用專案裡的裂縫影像）打過去，imgbb 回 `HTTP 400 Invalid API v1 key`，
代表端點、form-data、base64 編碼與錯誤訊息解析都正確，填入真 key 即可運作。

### 未處理

`CRACKpreLifeTime.py` 與 `manual-control-pygameLifeTime.py` 這兩個舊變體仍
`import pyimgur` 並讀 `config.ini`。它們是死程式碼——沒有任何 `.bat` 或模組
引用，且 `pyimgur` 已從 requirements 移除，直接執行會失敗。要留著就得一併
改寫，否則建議刪除。

⚠️ **舊金鑰視同外洩**：token 與 channel secret 曾長期以明文存在 `config.ini`
和 `LineBot.py` 裡。搬進 `.env` 只解決之後的問題，不會讓已經暴露的值變安全。
請到 LINE Developers 後台重新產生一組。

---

## 相容性

`PreImg(img_path_without_extension, channel)` 的行為未變：一定產生 `*pre.jpg`，
信心度 > 80% 才送 LINE，訊息格式沿用
`"The crack model confidence is over 80% in {N}%"`。
`LineSend` / `imgurUpload` / `SendEmail` 也都保留（`LineSend` 現在是非阻塞的）。
