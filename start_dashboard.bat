@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create the environment first: python -m venv .venv
  echo Then run: .venv\Scripts\python -m pip install -r requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py
pause
