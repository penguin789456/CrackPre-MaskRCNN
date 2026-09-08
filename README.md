# CrackPre 3.5.3 — 使用說明

用 Mask R-CNN 偵測混凝土裂縫。支援 Tello EDU、RTSP 串流、本機影片與既有圖片，
結果可手動推播到 LINE。

這份文件只講**怎麼裝、怎麼用**。模型本身、匯出流程與量測工具寫在
[docs/INFERENCE.md](docs/INFERENCE.md)，一般使用不需要看。

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

不需要 Conda，也不需要 C++ Build Tools。

---

## 步驟 1：取得程式碼

```bat
git clone https://github.com/penguin789456/CrackPre-MaskRCNN.git
cd CrackPre-MaskRCNN
```

---

## 步驟 2：安裝套件

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements\requirements-runtime.txt
pip install torch==2.7.0 torchvision==0.22.0
```

> `torchvision` 不能省。模型裡有 `torchvision::nms`，少了它載入會噴
> `Unknown builtin op: torchvision::nms`。

---

## 步驟 3：下載模型

模型檔 169 MB，超過 GitHub 單檔上限，所以不在 repo 裡，要另外下載。

**[⬇ model.ts](https://drive.google.com/file/d/1UDN8GwKFVPjjGAHShXcWFHHe4ugDRzpe/view?usp=sharing)**　放到 `models\model.ts`

或用 `gdown` 直接抓（Drive 對大檔會插一個病毒掃描確認頁，`curl`／`wget`
會抓到那頁的 HTML 而不是檔案，`gdown` 會自動處理）：

```bat
pip install gdown
gdown 1UDN8GwKFVPjjGAHShXcWFHHe4ugDRzpe -O models\model.ts
```

放好之後 `models\` 應該長這樣：

```
models\model.ts               169 MB  （剛下載的）
models\model_manifest.json    368 B   （repo 裡已經有，不用下載）
```

這樣就夠了。**不需要 `model_final.pth`，也不需要 detectron2** ——
那些是重新匯出模型才會用到的，見 [docs/INFERENCE.md](docs/INFERENCE.md)。

---

## 步驟 4：設定 LINE 通知（選用）

**只想先看偵測結果的話可以跳過。** 偵測與本機存檔不需要任何金鑰，
沒設定就只是「上傳到 LINE」那個按鈕會失敗。

```bat
copy .env.example .env
notepad .env
```

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

也可以之後在 App 裡按「設定」填，那邊有「測試連線」可以驗。

> **要拿到 Channel ID：** 跑 `python legacy\LineBot.py` 起本機伺服器，
> 用 `bin\ngrok.bat` 對外，把 ngrok 網址設進 LINE 後台的 Webhook，
> 然後對機器人發一則訊息，主控台就會印出 ID。

---

## 步驟 5：驗證安裝

```bat
python tools\selftest.py
```

不需要模型也不需要 `.env`。還沒設定 `.env` 的話金鑰檢查會顯示 `SKIP` 而不是失敗：

```
SKIP  整組 .env 檢查　（尚未建立 .env，見 README 步驟 4；不影響偵測功能）
全部通過（1 項略過：整組 .env 檢查）。
```

模型放好之後再跑這個，會實際載入模型並測試兩種模式：

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
| 🎥 **連續模式** | 接 Tello / RTSP / 影片，**處理不完的舊畫面會丟掉** | 現場即時巡檢 |

兩者丟不丟畫面的行為相反，所以做成啟動時詢問，而不是容易誤按的開關。

---

## 操作

### 主畫面

- 左邊是即時畫面，偵測到的裂縫會框起來、疊上遮罩並標上信心度
- **門檻**滑桿決定多高的信心度才算「命中」，預設 0.80
- 命中的畫面會存到 `%APPDATA%\CrackPre\hits`，並出現在右側清單

### 命中清單

選一張之後可以**開啟／上傳到 LINE／刪除**。

**上傳一律是手動的** —— 偵測過程中永遠不會自動推播。這是刻意的：連續模式對著
有裂縫的牆，自動推播會在幾秒內把 LINE 配額打爆。

上傳前記得在下拉選單選好要送到哪個 Channel。

### 圖片模式

按「選擇圖片…」挑檔案，或「選擇資料夾…」整批處理。狀態列會顯示進度
（第幾張／共幾張）。選幾張就處理幾張，不會略過。

### 連續模式

來源可選 **Tello EDU**、**RTSP 串流**、**影片檔**。

控制列有一個**行為下拉**加一個**「連線」**鈕。按「連線」就依下拉選的行為開始：

| 下拉選項 | 畫面 | 跑模型 | 用途 |
|---|---|---|---|
| **只預覽（不跑辨識）** | 來源原本的幀率（實測 22 fps） | ✗ | 對焦、取景、**操控無人機** |
| **開始辨識** | 跟著推論速度（CPU 上約 0.3 fps） | ✓ | 自動偵測並存下超過門檻的畫面 |

**飛行時請用「只預覽」** —— 辨識模式下畫面每三秒才動一次，那個速度沒辦法操控
無人機，等你看到障礙物時早就撞上了。（按「起飛」時會自動用只預覽開畫面。）

停止分成兩個，語意不同：

| | 作用 |
|---|---|
| **停止影像**（控制列） | 停掉抓影像／辨識。**Tello 仍連著，也還在飛** |
| **中斷連線**（飛行面板） | 真的斷開 Tello。仍在飛會先問要不要降落 |

狀態列在辨識模式會顯示「幀齡」與「已丟 N 幀」。**已丟數持續增加是正常的** ——
那代表系統在丟掉來不及處理的舊畫面，永遠分析最新的一格，延遲不會累積。

### 拍一張（按鈕或 `P` 鍵）

兩種模式下行為不同，但都會存進命中清單並標上 📷：

- **辨識模式** —— 目前畫面已經分析過了，立刻存檔（瞬間完成）。
  不看門檻也不受冷卻限制。
- **只預覽** —— 畫面沒跑過模型，所以會**單獨分析那一格**再存檔
  （CPU 上約 3 秒，期間按鈕顯示「分析中…」，畫面照樣流暢不會卡）。

這就是舊 `manual-control-pygame.py` 的工作流：用流暢畫面飛行，看到可疑處按一下
分析那一格。

> 快速鍵要先點一下畫面取得焦點才會生效（否則按鍵會被網址輸入框吃掉）。

沒有無人機也想試：

```bat
python tools\make_test_video.py
```

會用 `data\raw` 裡的圖做一支循環影片，在 GUI 裡來源選「影片檔」指過去即可。

### Tello 飛行操控

連續模式 + 來源選 Tello 時會出現飛行面板。直接按「起飛」即可 ——
**起飛會自動連線並開啟畫面**，不用先按別的。

| 鍵 | 動作 |
|---|---|
| ↑ ↓ | 前進 / 後退 |
| ← → | 左移 / 右移 |
| W / S | 上升 / 下降 |
| A / D | 左轉 / 右轉 |
| T | 起飛 |
| L | 降落 |
| P | 拍一張（存進命中清單） |

**按鍵要先點一下畫面取得焦點才會生效。** 面板上另有「降落 / 中斷連線 / 緊急停止」、
速度滑桿與電量／高度顯示。

> `P` 取代了舊 `manual-control-pygame.py` 按 `1`~`9` 拍單張的做法。
> 要送哪個 Channel 改成在命中清單那邊選，不再綁在按鍵上。

「緊急停止」會切斷馬達讓機體直接墜落，只在必要時使用（有確認對話框，不綁快速鍵）。

停止偵測不會自動降落，只會讓機體懸停。關閉視窗時若還在飛，會問你要不要先降落。

> ⚠️ **飛行操控尚未在真機驗證過。** 按鍵對應與狀態機有測試涵蓋，但開發環境沒有
> Tello，真機的起飛／降落反應還沒實測。第一次實飛請在空曠處、把速度調低、
> 隨時準備按 `L`。

---

## 疑難排解

**`RuntimeError: 沒有可用的推論後端`**
`models\` 裡沒有 `model.ts`。回到步驟 3。錯誤訊息會列出它找過哪些路徑。

**`Unknown builtin op: torchvision::nms`**
裝了 torch 但沒裝 torchvision。`pip install torchvision==0.22.0`。

**`ModuleNotFoundError: No module named 'PIL'`**
步驟 2 的 requirements 沒裝完整。pillow 是必需的。

**`OSError: [WinError 10048]`（Tello）**
UDP 8889 被占用 —— 通常是另一個 CrackPre 還開著。
用 `netstat -ano -p UDP | findstr 8889` 找出 PID 再關掉。

**Drive 下載回來是很小的 HTML 檔**
你抓到病毒掃描確認頁了。改用 `gdown`（見步驟 3）。

**「上傳到 LINE」失敗**
按 App 裡的「設定」→「測試連線」，會告訴你是 ImgBB 金鑰的問題還是 LINE 的問題。

**`.bat` 顯示 `conda 不是內部或外部命令`**
`legacy\*.bat` 預設要 conda。可以帶環境名稱當參數，例如 `RSTP.bat myenv`。
一般使用不需要那些舊腳本，用 `python main.py` 就好。

---

## 專案結構

```
main.py              桌面應用入口
crackpre_app/        PySide6 前端
core/                推論後端
tools/               命令列工具與測試
legacy/              舊的 OpenCV/pygame 入口腳本，仍可獨立執行
models/              模型（不進版控，見步驟 3）
data/raw/            影像資料
docs/                詳細文件
requirements/        依賴與安裝說明
third_party/         detectron2（只有匯出模型才需要）
bin/                 ngrok
```

---

## 文件

- [docs/INFERENCE.md](docs/INFERENCE.md) — 模型、匯出流程、後端與量測工具
- [docs/GUI.md](docs/GUI.md) — 桌面前端的架構與設計取捨
- [docs/OPTIMIZATION.md](docs/OPTIMIZATION.md) — 依賴瘦身與延遲最佳化的完整紀錄
- [docs/readme.txt](docs/readme.txt) — 原始的多語系說明
