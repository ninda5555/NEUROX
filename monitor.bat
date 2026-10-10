@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo NEUROX monitoring: http://localhost:8000
echo Keep this window open. Ctrl+C stops monitoring.
echo Broker setup is required before real market data can be collected.
.venv\Scripts\python.exe -m src.service
pause
