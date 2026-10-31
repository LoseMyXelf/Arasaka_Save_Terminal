@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto build
py -3 -c "import tkinter, venv" >nul 2>&1
if not errorlevel 1 (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
  goto build
)
python -c "import tkinter, venv" >nul 2>&1
if errorlevel 1 (
  echo Install Python 3 for Windows with Tkinter and pip, then try again.
  goto failed
)
python -m venv .venv
if errorlevel 1 goto failed
:build
".venv\Scripts\python.exe" -c "import tkinter"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath dist\portable --workpath build\portable ArasakaPortable.spec
if errorlevel 1 goto failed
echo.
echo Build complete: dist\portable\ArasakaSaveTerminal\ArasakaSaveTerminal.exe
echo Distribute the entire ArasakaSaveTerminal folder, including _internal.
pause
exit /b 0
:failed
echo.
echo Build failed. See the messages above for details.
pause
exit /b 1
