# CrackPre 3.5.3 — 混凝土裂縫偵測

以 Mask R-CNN（Detectron2 訓練、匯出為 TorchScript/ONNX）偵測混凝土裂縫。
支援 Tello EDU、RTSP 串流、本機影片與既有圖片，偵測結果可手動推播到 LINE。

- **PySide6 桌面應用**，開啟時詢問要用「圖片模式」或「連續模式」
- **執行期不需要 Detectron2**：模型匯出一次後，部署端只要 `onnxruntime`
- 金鑰集中在 `.env`，不進版控

---

## 快速開始

```bat
conda activate Chihlee
cd D:\CrackPreVer3.5.3
python main.py
```

啟動後先選模式：

| 模式 | 行為 | 適用 |
|---|---|---|
| 📁 **圖片模式** | 選圖片或資料夾，**每一張都處理，一張不漏** | 事後檢視已拍好的照片 |
| 🎥 **連續模式** | 接 Tello / RTSP / 影片，**處理不完的舊影格會丟掉** | 現場即時巡檢 |

兩者丟不丟幀的語意相反，所以做成啟動時詢問，而不是容易誤按的開關。

連續模式選 Tello 時會出現飛行操控面板（`↑↓←→` 移動、`W/S` 升降、`A/D` 轉向、
`T` 起飛、`L` 降落）。詳見 [docs/GUI.md](docs/GUI.md)。

---

## 安裝

### 部署端（只做推論）

不需要 conda、C++ Build Tools 或 Detectron2：

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements\requirements-runtime.txt
```

前提是 `models/` 裡已有匯出好的模型。見 `requirements/setUp-slim.txt`。

### 匯出端（要重新訓練或匯出模型）

```bat
conda env create -n CrackPre -f requirements\ComplateENV.yaml
conda activate CrackPre
pip install -e third_party\detectron2 --no-build-isolation
pip install -r requirements\requirements-export.txt
python tools\export_slim_model.py --format both
```

---

## 設定

金鑰全部放在專案根目錄的 `.env`（已被 `.gitignore` 排除）：

```bat
copy .env.example .env
notepad .env
python core\settings.py     :: 確認有讀到，金鑰會遮蔽顯示
```

需要的項目：`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_CHANNEL_SECRET`、
`LINE_CHANNEL_1..N`、`IMGBB_API_KEY`（到 <https://api.imgbb.com/> 免費申請）。

從舊版升級可用 `python tools\migrate_to_env.py --channel-secret <secret>`。

> Imgur API 已停用，圖片改上傳到 ImgBB。

---

## 專案結構

```
main.py              桌面應用入口
crackpre_app/        PySide6 前端（app / gui / engine / sources / worker /
                     tello_control / upload / settings_dialog / mode_dialog）
core/                推論後端（散裝模組，由 main.py 加進 sys.path）
                       crack_engine  單例引擎，onnx→torchscript→detectron2 自動挑選
                       visualize     OpenCV 繪製，取代 detectron2 Visualizer
                       pipeline      最新幀優先的 worker
                       settings      .env 解析（零依賴）
                       uploader      ImgBB 上傳
                       notifier      LINE 背景推播佇列
                       paths         路徑唯一真相來源
tools/               命令列工具（見下）
legacy/              舊的 OpenCV/pygame 入口腳本與 .bat，仍可獨立執行
models/              模型權重與匯出產物（大檔，不進版控）
data/raw/            影像資料（不進版控）
docs/                詳細文件
requirements/        依賴與安裝說明
third_party/         vendored detectron2（不進版控）
bin/                 ngrok
```

---

## 工具

```bat
python tools\selftest.py           :: 55 項單元測試，只要 numpy+opencv，不需模型
python main.py --selftest          :: GUI 無介面煙霧測試（兩種模式 + 飛行操控）
python tools\test_parity.py        :: 逐函式比對 detectron2 原版（需 detectron2）
python tools\compare_legacy.py     :: 80 張真實影像端到端精度回歸
python tools\bench_latency.py --legacy      :: 單張延遲，新舊對照
python tools\bench_continuous.py --fake --compare  :: 連續模式幀齡對照
python tools\make_test_video.py    :: 用既有圖片做測試影片，免無人機測連續模式
python tools\export_slim_model.py  :: 匯出 TorchScript / ONNX
```

---

## 已知限制

- **飛行操控未在真機驗證過**。按鍵對應、狀態機與連線共用邏輯有測試涵蓋，但開發
  環境沒有 Tello，真機的起飛／降落／rc 響應尚未實測。第一次實飛請在空曠處、
  低速、隨時準備按 `L`。
- **ONNX 後端未驗證**。TorchScript 後端已對 80 張真實影像做過逐位元比對；
  ONNX 匯出可行但尚未跑過精度回歸。引擎會自動退回可用的後端。
- `legacy/*LifeTime.py` 是無人引用的舊變體，仍依賴已移除的 `pyimgur`。
- 未提供 MSI 打包。

---

## 文件

- [docs/GUI.md](docs/GUI.md) — 桌面前端、兩種模式、Tello 飛行操控
- [docs/OPTIMIZATION.md](docs/OPTIMIZATION.md) — 依賴瘦身與延遲最佳化的完整紀錄，
  含實測數據與過程中抓到的缺陷
- [docs/readme.txt](docs/readme.txt) — 原始的多語系說明
