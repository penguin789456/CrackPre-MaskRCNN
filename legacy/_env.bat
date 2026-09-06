@echo off
chcp 65001 >nul
rem ---------------------------------------------------------------------------
rem Shared conda-environment activation, called by RSTP.bat /
rem manual-control-pygame.bat / windown_capture.bat.
rem Returns errorlevel 1 on failure; callers MUST check it.
rem
rem NOTE: keep this file pure ASCII. cmd.exe parses batch files using the
rem current codepage, and multi-byte UTF-8 text corrupts its line tokenizer,
rem which makes comment lines get executed as commands. Chinese belongs in
rem the Python scripts, not here. chcp 65001 above is still useful so the
rem Python side prints Chinese correctly.
rem
rem Environment name resolution order:
rem   1. first argument         e.g.  RSTP.bat Chihlee
rem   2. CRACK_ENV variable     e.g.  set CRACK_ENV=Chihlee
rem   3. default                CrackPre
rem ---------------------------------------------------------------------------

set "TARGET_ENV=%~1"
if "%TARGET_ENV%"=="" set "TARGET_ENV=%CRACK_ENV%"
if "%TARGET_ENV%"=="" set "TARGET_ENV=CrackPre"

rem Always run from this file's directory. config.ini, output\ and raw\ are
rem all relative paths, so launching from elsewhere would break them.
cd /d "%~dp0"

where conda >nul 2>&1
if errorlevel 1 goto :no_conda

echo Activating conda environment: %TARGET_ENV%
call conda activate "%TARGET_ENV%"

rem conda activate does not reliably set errorlevel, so compare the resulting
rem environment name instead.
if /i not "%CONDA_DEFAULT_ENV%"=="%TARGET_ENV%" goto :env_failed

rem Make sure python really comes from this environment and did not fall back
rem to some other python on PATH.
set "PY_PATH="
for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PY_PATH set "PY_PATH=%%P"
)
if not defined PY_PATH goto :no_python

echo Using python : %PY_PATH%
echo Working dir  : %CD%
echo.
exit /b 0

:no_conda
echo.
echo [ERROR] conda was not found on PATH.
echo         Launch from an Anaconda Prompt, or add conda to PATH.
echo.
exit /b 1

:no_python
echo.
echo [ERROR] No python found inside environment "%TARGET_ENV%".
echo.
exit /b 1

:env_failed
echo.
echo [ERROR] Could not activate conda environment: %TARGET_ENV%
echo         Currently active environment is: %CONDA_DEFAULT_ENV%
echo.
echo Available environments:
call conda env list
echo.
echo Pick one of these fixes:
echo   1. Pass an env name       RSTP.bat Chihlee
echo   2. Or set a variable      set CRACK_ENV=Chihlee
echo   3. Or create CrackPre     see setUp.txt in the project root
echo.
exit /b 1
