@echo off
REM Development: backend with auto-reload + Vite dev server with hot reload (http://localhost:5173)
start "JIMIKI backend" cmd /k "cd /d %~dp0backend && .venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
start "JIMIKI frontend" cmd /k "cd /d %~dp0frontend && npm run dev"
