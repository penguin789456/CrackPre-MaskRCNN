Environment Setup
Install C++ Build Tools: https://visualstudio.microsoft.com/visual-cpp-build-tools/

Install Conda.

Install Git.

Run the setUp command.

🔑 Secrets (Ver3.5.3+)
All secrets now live in .env at the project root, not config.ini and not hardcoded.

    cd CrackPre
    copy .env.example .env
    notepad .env            fill in token / secret / channel IDs / IMGBB_API_KEY
    python settings.py      verify (values are masked in the output)

Upgrading from an older version:
    python migrate_to_env.py --channel-secret <your LINE channel secret>

The Imgur API is disabled; images now upload to imgbb instead.
Get a free API key at https://api.imgbb.com/ and set IMGBB_API_KEY in .env.

WARNING: older versions kept the LINE access token in config.ini and the
channel secret hardcoded in LineBot.py. Those values are no longer protected —
regenerate them in the LINE Developers console before putting them in .env.

🔧 Before Using
Set your ngrok Auth Token: How to use ngrok plugin

Run ngrok.bat.

Configure the ngrok URL in the LINE developer console so the LINE server can communicate with your local server via ngrok.

Add the LINE bot as a friend or invite it to any group.

When any user sends a message, the System User will see the group ID or user ID on the console.

Copy that ID and add it to your config file in the specified format.

Usage
Must be connected to the drone's local network.

Run either CrackPre/manual-control-pygame.bat or CrackPre/RTSP.bat:

manual-control-pygame.bat is used for Tello EDU.

RTSP.bat is for RTSP streaming source.

Notice
Video frame communication happens over LAN, but a separate internet connection is required to send data packages to the cloud.

zh-cn
环境配置
安装 C++ 构建工具：https://visualstudio.microsoft.com/visual-cpp-build-tools/

安装 Conda。

安装 Git。

运行 setUp 命令。

🔧 使用前设置
设置你的 ngrok 授权令牌：如何使用 ngrok 插件

运行 ngrok.bat。

在 LINE 开发者控制台 中设置 ngrok URL，使 LINE 服务器能通过 ngrok 访问本地服务器。

添加机器人为好友，或将其加入任意群组。

当用户发送消息时，控制台会显示用户 ID 或群组 ID。

复制该 ID，并按配置格式添加至配置文件中。

▶️ 使用方法
必须连接至 无人机所在的本地网络。

启动 CrackPre/manual-control-pygame.bat 或 CrackPre/RTSP.bat：

manual-control-pygame.bat 用于 Tello EDU。

RTSP.bat 用于 RTSP 视频源。

⚠️ 注意事项
视频画面传输是通过 局域网 实现的，但仍需要 另一条互联网连接 来将数据包发送至云端。

zh-tw
環境設定
安裝 C++ 編譯工具：https://visualstudio.microsoft.com/visual-cpp-build-tools/

安裝 Conda。

安裝 Git。

執行 setUp 指令。

🔧 使用前準備
設定你的 ngrok 授權碼：如何使用 ngrok 插件

執行 ngrok.bat。

在 LINE 開發者平台 設定 ngrok URL，使 LINE 伺服器可透過 ngrok 與本地伺服器溝通。

將機器人加入好友，或邀請進入任何群組。

當有使用者發送訊息時，控制台會顯示使用者 ID 或群組 ID。

複製該 ID，並依格式新增至設定檔中。

▶️ 使用方式
必須連接至 無人機所在的區域網路。

執行 CrackPre/manual-control-pygame.bat 或 CrackPre/RTSP.bat：

manual-control-pygame.bat 適用於 Tello EDU。

RTSP.bat 適用於 RTSP 串流來源。

⚠️ 注意事項
影像傳輸 是透過 區域網路（LAN） 進行，但仍需 額外的網際網路連線 將資料封包傳送至雲端。



#option
send_command_without_return 
Comment self.LOGGER.info() will make console is clean
on some time conda env can't read detectron2 don't know why on this situation Run Fixdetectron2.bat


Ver3.3->Update
Include WinGui Windown capture insert to Model

Ver3.5.3->Update (依賴瘦身 / 延遲最佳化)
詳見 OPTIMIZATION.md。

部署端不再需要 conda、C++ Build Tools 或 detectron2：
  1. 匯出端跑一次 CrackPre/export_slim_model.py   -> output/model.onnx
  2. 部署端照 setUp-slim.txt 安裝 requirements-runtime.txt
  3. cd CrackPre && python selftest.py            -> 驗證安裝
     cd CrackPre && python bench_latency.py       -> 量測延遲

RTSP.py 已改用 OpenCV FFmpeg backend，不再需要 GStreamer / PyGObject。
舊的 GStreamer 版本保留在 RTSP_gstreamer.py。

RTSP.py 操作鍵：1~9 送出目前畫面到對應 Channel、c 切換連續判別、q 離開。

可用環境變數：
  CRACK_BACKEND=onnx|torchscript|detectron2|auto   選擇推論後端（預設 auto）
  CRACK_MIN_SIZE / CRACK_MAX_SIZE                  輸入解析度（預設 800 / 1333）
  CRACK_SCORE_THRESH                               偵測門檻（預設 0.5）
  CRACK_ALERT_THRESH                               送 LINE 的門檻（預設 0.8）
  CRACK_CONTINUOUS_INTERVAL                        連續模式最小間隔秒數（預設 0.2）
  CRACK_RTSP_URL                                   RTSP 來源網址
