@echo off
REM Daily launcher for Windows. Double-click each trading morning.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m src.run
pause
