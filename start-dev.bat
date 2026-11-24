@echo off
REM Development mode: server with auto-reload (Python and template changes). Open http://127.0.0.1:8765
setlocal
cd /d "%~dp0backend"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
