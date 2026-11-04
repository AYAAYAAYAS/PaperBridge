@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\20261104-toolbar\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\20261104-toolbar\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if exist "%~dp0dist\20261104-drag\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\20261104-drag\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if exist "%~dp0dist\20261104-compact\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\20261104-compact\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if exist "%~dp0dist\20261104-notes\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\20261104-notes\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if exist "%~dp0dist\20261104-ui\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\20261104-ui\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if exist "%~dp0dist\20261104\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\20261104\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if exist "%~dp0dist\20261021\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\20261021\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if exist "%~dp0dist\20261013\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\20261013\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if exist "%~dp0dist\20260922\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\20260922\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if exist "%~dp0dist\updated\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\updated\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if exist "%~dp0dist\PaperBridge\PaperBridge.exe" (
    start "" "%~dp0dist\PaperBridge\PaperBridge.exe" %*
    exit /b
)
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Please run the dependency installer first.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py" %*
