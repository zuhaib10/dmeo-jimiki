@echo off
REM JIMIKI Image Studio - one-time setup (Windows). Requires Python 3.11+ and Node.js 20+.
setlocal
cd /d "%~dp0backend"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'" || goto :error
if not exist .venv ( %PY% -m venv .venv || goto :error )
call .venv\Scripts\python -m pip install --upgrade pip || goto :error
call .venv\Scripts\python -m pip install -r requirements.txt || goto :error
if not exist .env copy .env.example .env >nul
cd /d "%~dp0frontend"
call npm install || goto :error
call npm run build || goto :error
echo.
echo Setup complete. Edit backend\.env (OPENAI_API_KEY) then run run.bat
exit /b 0
:error
echo Setup failed.
exit /b 1
