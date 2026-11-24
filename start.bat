@echo off
REM DevGrowth — one-click start (Windows). Opens http://127.0.0.1:8765
setlocal
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo [setup] Creating Python virtual environment...
  python -m venv backend\.venv || goto :error
  backend\.venv\Scripts\python -m pip install --upgrade pip >nul
  backend\.venv\Scripts\python -m pip install -r backend\requirements.txt || goto :error
)

echo.
echo  DevGrowth is starting at http://127.0.0.1:8765   (Ctrl+C to stop)
echo  Your data: %~dp0backend\data\skilldev.db
echo.
start "" http://127.0.0.1:8765
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
goto :eof

:error
echo.
echo Setup failed. See README.md ("Troubleshooting").
pause
