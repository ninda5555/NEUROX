@echo off
REM First-time data download + model training. Double-click once (takes 1-3 hours).
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m src.scripts.bootstrap
pause
