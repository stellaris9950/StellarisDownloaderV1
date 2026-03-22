$ErrorActionPreference = "Stop"

python -m PyInstaller --noconfirm --clean .\StellarisModManager.spec

Write-Host "Build complete. Output folder: dist\StellarisModManager"
