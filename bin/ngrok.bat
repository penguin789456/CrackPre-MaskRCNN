@echo off
rem 用同資料夾裡的 ngrok.exe，不依賴 PATH
"%~dp0ngrok.exe" http 5000
