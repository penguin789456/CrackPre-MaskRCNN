@echo off
rem Tello EDU manual control + crack detection.  Usage: manual-control-pygame.bat [conda-env-name]
call "%~dp0_env.bat" %1
if errorlevel 1 goto :end
python manual-control-pygame.py
:end
pause
