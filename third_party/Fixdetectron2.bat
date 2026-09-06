@echo off
rem 重新安裝 vendored 的 detectron2（conda 環境偶爾會讀不到它）。
rem 用法: Fixdetectron2.bat [conda環境名稱]，預設 CrackPre。
cd /d "%~dp0"
set "TARGET_ENV=%~1"
if "%TARGET_ENV%"=="" set "TARGET_ENV=CrackPre"
call conda activate "%TARGET_ENV%"
if /i not "%CONDA_DEFAULT_ENV%"=="%TARGET_ENV%" (
    echo [ERROR] Could not activate conda environment: %TARGET_ENV%
    pause
    exit /b 1
)
pip install -e detectron2 --no-build-isolation
pause
