@echo off
REM One-time setup for Windows. Double-click once after downloading the project.
cd /d "%~dp0"

echo ==^> Creating Python virtual environment (.venv)
python -m venv .venv
call .venv\Scripts\activate.bat

echo ==^> Installing Python packages (this takes a few minutes)
python -m pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if not exist config.yaml (
  copy config.example.yaml config.yaml
  echo ==^> Created config.yaml - OPEN IT and paste your Fyers app_id + secret_key.
)

echo.
echo Setup done. Next:
echo   1. Put your Fyers keys in config.yaml
echo   2. Double-click:  bootstrap.bat   (first-time data + model training)
echo   3. Every morning after that:  start.bat
pause
