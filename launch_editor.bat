@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" CyberpunkSaveEditor.py
  goto result
)
py -3 -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
  py -3 CyberpunkSaveEditor.py
  goto result
)
python -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
  python CyberpunkSaveEditor.py
  goto result
)
echo Python 3 with Tkinter was not found. Install Python for Windows and try again.
pause
exit /b 1
:result
if errorlevel 1 (
  echo Launch failed. See the error above.
  pause
  exit /b 1
)
exit /b 0
