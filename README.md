# CrackPre 3.5.3 — 混凝土裂縫偵測

用 Mask R-CNN 偵測混凝土裂縫。支援 Tello EDU、RTSP 串流、本機影片與既有圖片，
結果可手動推播到 LINE。附一個 PySide6 桌面應用，開啟時選「圖片模式」或「連續模式」。

這份說明假設你**手上什麼都沒有**，從安裝 Python 開始一路走到程式跑起來。

---

## ⚠️ 先看這裡：clone 下來還不能直接跑

模型權重（169 MB）超過 GitHub 單檔 100 MB 上限，**不在這個 repo 裡**。
`third_party/detectron2` 與 `data/raw` 也沒有進版控。

所以剛 clone 完會缺三樣東西，下面步驟 3 會處理：

| 缺什麼 | 檔案 | 怎麼拿 |
|---|---|---|
| 模型權重 | `models/model_final.pth` | [Google Drive](https://drive.google.com/file/d/1yq8iHWtnphvVG0s8zuP1mRz0EYuEUywJ/view?usp=sharing)（步驟 3-1） |
| 推論用模型 | `models/model.ts` 或 `model.onnx` | 由權重匯出（步驟 3-2） |
| detectron2 | `third_party/detectron2/` | 只有「要匯出模型」時才需要，步驟 3-2 會 clone |

---

## 步驟 0：事前準備

| 需要 | 版本 | 說明 |
|---|---|---|
| Windows | 10 / 11 | 視窗擷取與 `.bat` 是 Windows 專用 |
| Python | **3.12**（3.11 也可） | [python.org](https://www.python.org/downloads/) 下載，安裝時勾選 **Add Python to PATH** |
| Git | 任意 | [git-scm.com](https://git-scm.com/download/win) |

確認：

```bat
python --version
git --version
```

> 只有在你要**自己重新匯出模型**時，才另外需要
> [C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
> 與 Conda。只做推論不需要。

---

## 步驟 1：取得程式碼

```bat
git clone https://github.com/penguin789456/CrackPre-MaskRCNN.git
cd CrackPre-MaskRCNN
```

---

## 步驟 2：建立 Python 環境

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements\requirements-runtime.txt
```

接著看你要走哪條路線裝推論後端：

### 路線 A — 最快能跑（建議先用這條）

用 TorchScript 模型，需要 torch。約 2.5 GB，但**不需要 detectron2，也不需要編譯器**。

```bat
pip install torch==2.7.0 torchvision==0.22.0
```

> `torchvision` 不能省。匯出的計算圖裡有 `torchvision::nms`，少了它載入會噴
> `Unknown builtin op: torchvision::nms`。

### 路線 B — 最輕量

只要 `onnxruntime`（約 250 MB，已包含在上面的 requirements 裡），不必裝 torch。
但**目前 repo 沒有現成的 `model.onnx`，你得自己匯出一次**（見步驟 3-2），
而匯出需要完整的 detectron2 環境。

> 老實說：ONNX 這條路的匯出流程可行，但**還沒有跑過精度回歸驗證**。
> TorchScript 那條已經對 80 張真實影像做過逐位元比對。要求穩定請先走路線 A。

---

## 步驟 3：取得模型

### 3-1　下載權重

從 Google Drive 取得 `model_final.pth`（約 169 MB）：

**https://drive.google.com/file/d/1yq8iHWtnphvVG0s8zuP1mRz0EYuEUywJ/view?usp=sharing**

下載後放到 `models\`：

```
models\model_final.pth
```

也可以用 `gdown` 直接抓（Drive 對大檔會擋掉 `curl`／`wget` 的病毒掃描確認頁，
`gdown` 會自動處理）：

```bat
pip install gdown
gdown 1yq8iHWtnphvVG0s8zuP1mRz0EYuEUywJ -O models\model_final.pth
```

沒有這個檔就沒辦法偵測。

### 3-2　匯出成推論格式

Drive 上放的是 `.pth`（訓練權重），程式**不能直接用它推論**，要先匯出一次。

如果你已經拿到 `model.ts` / `model.onnx` + `model_manifest.json`（例如從別台
匯出過的機器複製過來），直接放進 `models\` 就好，**跳過這一小節**，
也不需要 detectron2。

否則要從 `.pth` 匯出，這一步需要完整環境：

```bat
:: 取得 detectron2（v0.6）
git clone https://github.com/facebookresearch/detectron2.git third_party\detectron2

:: 建立匯出用的 conda 環境（需要 C++ Build Tools）
conda env create -n CrackPreExport -f requirements\ComplateENV.yaml
conda activate CrackPreExport
pip install -e third_party\detectron2 --no-build-isolation
pip install -r requirements\requirements-export.txt

:: 匯出
python tools\export_slim_model.py --format both
```

產出 `models\model.ts`、`models\model.onnx`、`models\model_manifest.json`。
匯出腳本會拿同一張圖比對 detectron2 與匯出模型的 box / score，
**誤差超標會直接報錯** —— 不要拿沒驗證過的模型上線。

匯出完就可以離開這個環境，回到步驟 2 的 `.venv`。之後都不再需要 detectron2。

---

## 步驟 4：設定金鑰

只有「上傳到 LINE」需要。**只想先看偵測結果的話可以跳過**，偵測與本機存檔都能正常用。

```bat
copy .env.example .env
notepad .env
```

填這些：

| 變數 | 哪裡拿 |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | [LINE Developers](https://developers.line.biz/) → Messaging API → Channel access token |
| `LINE_CHANNEL_SECRET` | 同上 → Basic settings → Channel secret |
| `LINE_CHANNEL_1` …`_N` | 推播目標。使用者 ID 以 `U` 開頭、群組 ID 以 `C` 開頭 |
| `IMGBB_API_KEY` | <https://api.imgbb.com/> 免費申請（Imgur API 已停用） |

確認有讀到（金鑰會遮蔽顯示）：

```bat
python core\settings.py
```

> 要拿到 Channel ID：跑 `python legacy\LineBot.py` 起本機伺服器，用
> `bin\ngrok.bat` 對外，把 ngrok 網址設進 LINE 後台的 Webhook，
> 然後對機器人發一則訊息，主控台就會印出 ID。

---

## 步驟 5：驗證安裝

```bat
python tools\selftest.py
```

**不需要模型權重、也不需要 `.env`**（步驟 2 裝好就能跑）。還沒設定 `.env` 的話，
金鑰相關的檢查會顯示 `SKIP` 而不是失敗：

```
SKIP  整組 .env 檢查　（尚未建立 .env，見 README 步驟 4；不影響偵測功能）
全部通過（1 項略過：整組 .env 檢查）。
```

設定 `.env` 之後再跑一次就會變成 `全部通過。`（無略過）。

有模型之後再跑這個 —— 會實際載入模型，並測試兩種模式與飛行操控：

```bat
python main.py --selftest
```

---

## 步驟 6：執行

```bat
python main.py
```

開啟時先選模式：

| 模式 | 行為 | 適用 |
|---|---|---|
| 📁 **圖片模式** | 選圖片或資料夾，**每一張都處理，一張不漏** | 事後檢視已拍好的照片 |
| 🎥 **連續模式** | 接 Tello / RTSP / 影片，**處理不完的舊影格會丟掉** | 現場即時巡檢 |

兩者丟不丟幀的語意相反，所以做成啟動時詢問，而不是容易誤按的開關。

偵測到超過門檻的畫面會存到 `%APPDATA%\CrackPre\hits` 並出現在右側清單。
**上傳一律手動** —— 選一張按「上傳到 LINE」。偵測過程中永遠不會自動推播。

### 沒有無人機也想試連續模式

```bat
python tools\make_test_video.py
```

會用 `data\raw` 的圖做一支循環影片；在 GUI 裡來源選「影片檔」指過去即可。

### Tello 飛行操控

連續模式 + 來源選 Tello 時會出現飛行面板：

| 鍵 | 動作 |
|---|---|
| ↑ ↓ | 前進 / 後退 |
| ← → | 左移 / 右移 |
| W / S | 上升 / 下降 |
| A / D | 左轉 / 右轉 |
| T / L | 起飛 / 降落 |

按鍵要先點一下畫面取得焦點。詳見 [docs/GUI.md](docs/GUI.md)。

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
                       settings      .env 解析（零依賴，不需要 python-dotenv）
                       uploader      ImgBB 上傳
                       notifier      LINE 背景推播佇列
                       paths         路徑唯一真相來源
tools/               命令列工具與測試
legacy/              舊的 OpenCV/pygame 入口腳本與 .bat，仍可獨立執行
models/              模型權重與匯出產物（不進版控）
data/raw/            影像資料（不進版控）
docs/                詳細文件
requirements/        依賴與安裝說明
third_party/         detectron2（不進版控）
bin/                 ngrok
```

---

## 工具

```bat
python tools\selftest.py                          單元測試，不需模型也不需 .env
python main.py --selftest                         GUI 煙霧測試（兩模式 + 飛行操控）
python tools\test_parity.py                       逐函式比對 detectron2（需 detectron2）
python tools\compare_legacy.py                    80 張影像端到端精度回歸
python tools\bench_latency.py --legacy            單張延遲，新舊對照
python tools\bench_continuous.py --fake --compare  連續模式幀齡對照
python tools\make_test_video.py                   做測試影片，免無人機測連續模式
python tools\export_slim_model.py --format both    匯出 TorchScript / ONNX
python tools\migrate_to_env.py                    從舊版 config.ini 搬金鑰
```

---

## 疑難排解

**`RuntimeError: 沒有可用的推論後端`**
`models\` 裡沒有任何可用模型。回到步驟 3。錯誤訊息會列出它找過哪些路徑。

**`Unknown builtin op: torchvision::nms`**
裝了 torch 但沒裝 torchvision。`pip install torchvision==0.22.0`。

**`OSError: [WinError 10048]`（Tello）**
UDP 8889 被占用 —— 通常是另一個 CrackPre 還開著。
用 `netstat -ano -p UDP | findstr 8889` 找出 PID 再關掉。

**GUI 中文變成方框**
Qt 找不到字型。正常在 Windows 桌面執行不會發生；用 offscreen 模式算圖才會。

**`.bat` 顯示 `conda 不是內部或外部命令`**
從 Anaconda Prompt 執行，或把 conda 加入 PATH。
`legacy\*.bat` 可以帶環境名稱當參數，例如 `RSTP.bat myenv`。

---

## 已知限制

- **飛行操控未在真機驗證過。** 按鍵對應、狀態機與連線共用邏輯有測試涵蓋，
  但開發環境沒有 Tello，真機的起飛／降落／rc 響應尚未實測。
  第一次實飛請在空曠處、低速、隨時準備按 `L`。
- **ONNX 後端未做精度回歸。** 匯出流程可行，但只有 TorchScript 那條對 80 張
  真實影像做過逐位元比對。
- `legacy\*LifeTime.py` 是無人引用的舊變體，仍依賴已移除的 `pyimgur`，直接執行會失敗。
- 未提供 MSI 打包。

---

## 文件

- [docs/GUI.md](docs/GUI.md) — 桌面前端、兩種模式、Tello 飛行操控
- [docs/OPTIMIZATION.md](docs/OPTIMIZATION.md) — 依賴瘦身與延遲最佳化的完整紀錄，
  含實測數據與過程中抓到的缺陷
- [docs/readme.txt](docs/readme.txt) — 原始的多語系說明
