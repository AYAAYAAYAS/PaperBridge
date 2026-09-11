@echo off
setlocal
cd /d "%~dp0"
python -m venv "%~dp0.venv"
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" -m pip install --no-cache-dir -r "%~dp0requirements.txt"
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" -m paperbridge.assets
if errorlevel 1 goto failed
echo Installation completed. You can now launch PaperBridge.
pause
exit /b 0
:failed
echo Installation failed. Please check Python 3.10-3.13 and network access.
pause
exit /b 1
