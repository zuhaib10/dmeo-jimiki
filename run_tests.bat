@echo off
cd /d "%~dp0backend"
.venv\Scripts\python -m pytest -q
