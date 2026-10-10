@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe -m src.scripts.configure_broker
pause
