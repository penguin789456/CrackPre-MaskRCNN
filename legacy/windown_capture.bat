@echo off
rem Windows window capture + crack detection.  Usage: windown_capture.bat [conda-env-name]
call "%~dp0_env.bat" %1
if errorlevel 1 goto :end
python WINDOWNCATCH.py
:end
pause
