$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $pythonPath -m pip install 'pyinstaller==6.22.2'
if ($LASTEXITCODE -ne 0) { throw 'Cannot install packaging tools.' }
$distPath = Join-Path $PSScriptRoot 'dist\20261013'
& $pythonPath -m PyInstaller --noconfirm --clean --windowed --onedir --name PaperBridge --distpath $distPath --exclude-module babeldoc --exclude-module scipy --exclude-module numpy main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
# Python 3.10 ships an older VC runtime. Qt 6.11 requires its bundled newer runtime.
# Keep the same version at the bundle root so Python cannot preload an older DLL.
$qtRoot = Join-Path $PSScriptRoot '.venv\Lib\site-packages\PySide6'
$bundleRoot = Join-Path $distPath 'PaperBridge\_internal'
Get-ChildItem -LiteralPath $qtRoot -Filter '*140*.dll' | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $bundleRoot -Force
}
# Qt uses Windows' unversioned ICU API. A Git installation on PATH can otherwise
# make PyInstaller bundle a different ICU with only suffixed exports.
@('icuuc.dll', 'icudt78.dll') | ForEach-Object {
    $bundledIcu = Join-Path $bundleRoot $_
    if (Test-Path -LiteralPath $bundledIcu) { Remove-Item -LiteralPath $bundledIcu }
}
Write-Host 'Built: dist\20261013\PaperBridge\PaperBridge.exe. Keep this project folder and .venv together.'
