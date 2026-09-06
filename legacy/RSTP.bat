@echo off
rem RTSP stream crack detection.  Usage: RSTP.bat [conda-env-name]
call "%~dp0_env.bat" %1
if errorlevel 1 goto :end
python RTSP.py
:end
pause
