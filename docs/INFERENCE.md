# 推論：模型、匯出與驗證

這份是給要**重新匯出模型、換模型、或想了解推論管線**的人看的。
只是要把 App 裝起來用的話，看 [README.md](../README.md) 就夠了 ——
那條路只需要下載 `model.ts`，不需要這裡的任何東西。

---

## 1. 模型

Mask R-CNN R50-FPN（Detectron2 訓練），三個裂縫類別：

```python
["diagonal_crack", "horizontal_crack", "vertical_crack"]
```

| 設定 | 值 |
|---|---|
| 輸入 | BGR、`ResizeShortestEdge(800, max 1333)`、CHW float32 未正規化 |
| 正規化 | 在計算圖內（`PIXEL_MEAN` BGR `[103.530, 116.280, 123.675]`） |
| 輸出 | `pred_boxes` / `pred_classes` / `pred_masks`(N,1,28,28) / `scores` / `image_size` |
| 偵測門檻 | `SCORE_THRESH_TEST = 0.5` |

輸出順序記在 `models/model_manifest.json`，執行期由 `core/crack_engine.py` 讀取——
不是硬編在程式裡，換模型時比較不會對錯欄位。

---

## 2. 三個後端

`core/crack_engine.py` 依序自動挑選，找到可用的就停：

| 後端 | 需要 | 大小 | 狀態 |
|---|---|---|---|
| `onnx` | `onnxruntime` | ~250 MB | **未做精度回歸** |
| `torchscript` | `torch` + `torchvision` | ~2.5 GB | 已驗證，逐位元相符 |
| `detectron2` | `torch` + `detectron2` + 編譯器 | ~6 GB | 原始路徑，fallback |

用 `CRACK_BACKEND=onnx|torchscript|detectron2|auto` 指定，預設 `auto`。

前處理（`ResizeShortestEdge`）與後處理（box 還原、28×28 ROI mask 貼回原圖）
在 `crack_engine.py` 裡用純 numpy／OpenCV 重寫，所以前兩個後端**完全不 import
detectron2**。這是整個依賴瘦身的關鍵。

---

## 3. 重新匯出

### 3-1　取得訓練權重

**[⬇ model_final.pth](https://drive.google.com/file/d/1yq8iHWtnphvVG0s8zuP1mRz0EYuEUywJ/view?usp=sharing)**（169 MB）→ `models\model_final.pth`

```bat
pip install gdown
gdown 1yq8iHWtnphvVG0s8zuP1mRz0EYuEUywJ -O models\model_final.pth
```

### 3-2　建立匯出環境

這一步需要 **Conda** 與 [C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)——
detectron2 與 pycocotools 都要現場編譯。這也正是為什麼部署端不該碰這條路。

```bat
git clone https://github.com/facebookresearch/detectron2.git third_party\detectron2

conda env create -n CrackPreExport -f requirements\ComplateENV.yaml
conda activate CrackPreExport
pip install -e third_party\detectron2 --no-build-isolation
pip install -r requirements\requirements-export.txt
```

conda 環境偶爾會讀不到 detectron2，這時跑 `third_party\Fixdetectron2.bat`。

### 3-3　匯出

```bat
python tools\export_slim_model.py --format both
```

產出 `models\model.ts`、`models\model.onnx`、`models\model_manifest.json`。

匯出腳本會拿同一張圖比對 detectron2 與匯出模型的 box / score，
**誤差超標直接報錯**。不要拿沒驗證過的模型上線。

匯出時會噴一大堆 `TracerWarning`，那是 Mask R-CNN tracing 的常態，
**但不能當背景雜訊忽略** —— 它代表控制流被固定成常數，換一個輸入尺寸就可能算錯。
所以下一節的跨解析度回歸是必要的，不是可選的。

匯出完就可以離開這個環境，之後都不再需要 detectron2。

---

## 4. 驗證

三層，由快到慢：

```bat
python tools\selftest.py         :: 秒級，不需模型也不需 detectron2
python tools\test_parity.py      :: 秒級，需 detectron2。逐函式比對數值
python tools\compare_legacy.py --backend torchscript --save-diff   :: ~10 分鐘
```

- **`test_parity.py`** — 把 `crack_engine` 的 numpy 實作跟 detectron2 的 torch
  原版擺在一起逐函式比。端到端測試只會說「有差」，這支會說「差在哪一段」。
- **`compare_legacy.py`** — 拿 `data/raw` 的真實影像跑舊路徑 vs 新後端。
  `--save-diff` 會把超標的影像並排存到 `data/raw/_diff/`。

### 已驗證的結果

TorchScript 後端，80 張真實影像（RTSP 720p、TELLO、WINDOWSCATCH 三種解析度）：

```
最大 score 差 : 0.00000   最大 box 差 : 0.000 px
最小 mask IoU : 1.0000    偵測數不符 : 0 張
判定：通過。
```

逐位元相同。這同時回答了 tracing 泛化的疑慮 —— 在 720p 上 trace 的圖換到其他
解析度仍然正確。

**ONNX 後端還沒跑過這個回歸。** 匯出流程可行，但要上線請先自己驗一次。

---

## 5. 前後處理的三個坑

這些是端到端回歸抓出來的實際缺陷，改模型時容易再踩：

**1. Resize 必須用 PIL 的 BILINEAR，不能用 `cv2.INTER_LINEAR`。**
detectron2 的 `ResizeTransform.apply_image` 對 uint8 走的是
`Image.fromarray(img).resize(..., Image.BILINEAR)`，那是帶 support scaling 的
重採樣濾波，跟 OpenCV 不同。用錯的話送進網路的張量從一開始就不一樣，
**score 會漂 0.0157~0.0448**，連帶讓偵測數與類別對不上。

**2. `detector_postprocess` 的順序是 scale → clip → 濾掉退化 box → 貼 mask。**
中間那步 `nonempty()` 漏掉的話偵測數會多出來。

**3. Mask 貼回要用 `grid_sample(align_corners=False)` 的對齊方式**，
以像素中心 (+0.5) 對應到**浮點數** box 的正規化座標。用「crop 成整數框再
`cv2.resize`」會讓細長的裂縫 IoU 掉到 0.85~0.92。

教訓：單張圖、單一解析度的「誤差 0.0000」證明不了任何事 —— 那張圖正好就是
tracing 的樣本。要跨解析度、跨數十張才有意義。

---

## 6. 效能量測

```bat
python tools\bench_latency.py --n 20 --legacy          :: 單張延遲，新舊對照
python tools\bench_continuous.py --fake --compare      :: 連續模式幀齡對照
```

看 **p50 / p95**，不要只看平均 —— 平均會被第一次的模型載入洗掉。

### 單張延遲

舊版每呼叫一次就 `DefaultPredictor(cfg)` 一次，重建計算圖並從磁碟讀 169 MB 權重。
改成 process 內單例後（CPU 實測）：

```
單例 / detectron2       : p50 3358.8 ms
舊版 / 每張重建 predictor : p50 4170.8 ms
```

這台沒有 CUDA，推論本身就吃掉 3.3 秒把比例壓平了。**有 GPU 的機器上比例會完全
不同** —— 推論掉到百毫秒級，而重建成本不會變小反而更貴。請在實際部署的機器上重跑。

### 連續模式

單張延遲量不到連續模式真正的問題：**延遲會不會越積越多**。
關鍵指標是**幀齡** —— 影格送出到真的開始處理之間隔了多久。

| | 最新幀優先 | 全部排隊（舊行為） |
|---|---|---|
| 幀齡 p50 | **12.2 ms** | **10302.4 ms** |
| 幀齡 / 推論時間 | 0.04x | 34.30x |

排隊模式「處理張數」比較多，但那不是優點 —— 每個結果都對應到十秒前的畫面。

### 調快一點

```bat
set CRACK_MIN_SIZE=640          :: 預設 800，近乎線性換延遲
set CRACK_MAX_SIZE=1024         :: 預設 1333
set CRACK_BACKEND=onnx
set CRACK_CONTINUOUS_INTERVAL=0.3
```

代價是小裂縫召回率下降，調完請用實際資料確認。

---

## 7. 環境變數

| 變數 | 預設 | 作用 |
|---|---|---|
| `CRACK_BACKEND` | `auto` | `onnx` / `torchscript` / `detectron2` |
| `CRACK_MIN_SIZE` | `800` | 短邊縮放目標 |
| `CRACK_MAX_SIZE` | `1333` | 長邊上限 |
| `CRACK_SCORE_THRESH` | `0.5` | 偵測門檻 |
| `CRACK_ALERT_THRESH` | `0.8` | 判定為「命中」的門檻 |
| `CRACK_CONTINUOUS_INTERVAL` | `0.2` | 連續模式兩次推論最小間隔（秒） |
| `CRACK_RTSP_URL` | `rtsp://192.168.144.25:8554/main.264` | 舊 `legacy/RTSP.py` 的來源 |
| `CRACK_VERBOSE` | `1` | 設 `0` 關掉引擎的載入訊息 |

---

## 相關文件

- [../README.md](../README.md) — 安裝與使用
- [GUI.md](GUI.md) — 桌面前端架構
- [OPTIMIZATION.md](OPTIMIZATION.md) — 依賴瘦身與延遲最佳化的完整紀錄
