@echo off
REM One-time setup for Windows. Double-click once after downloading the project.
cd /d "%~dp0"

echo ==^> Creating Python virtual environment (.venv)
python -m venv .venv
if errorlevel 1 goto :failed
call .venv\Scripts\activate.bat

echo ==^> Installing Python packages (this takes a few minutes)
python -m pip install --require-hashes -r requirements.lock
if errorlevel 1 goto :failed

if not exist config.yaml (
  copy config.example.yaml config.yaml
  echo ==^> Created config.yaml - OPEN IT and paste your Fyers app_id + secret_key.
)

echo.
echo Setup done. Next:
echo   1. Put your Fyers keys in config.yaml
echo   2. Double-click:  bootstrap.bat   (first-time data setup)
echo   3. Every morning after that:  start.bat
pause
exit /b 0

:failed
echo Setup failed. Read the error above; setup is not complete.
pause
exit /b 1
