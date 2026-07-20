@echo off
setlocal
cd /d "%~dp0"
if not exist .venv uv sync --extra dev || exit /b 1
if not exist .env copy .env.example .env >nul
start "" /b .venv\Scripts\python.exe -c "import time,webbrowser; time.sleep(2); webbrowser.open('http://127.0.0.1:7474')"
set PYTHONPATH=%CD%\src
.venv\Scripts\python.exe -m uvicorn reviewer.app:app --host 127.0.0.1 --port 7474
