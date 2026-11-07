$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host 'GameVault - construction du serveur Windows' -ForegroundColor Cyan
python -m pip install --upgrade pyinstaller

if (Test-Path 'build-server') { Remove-Item 'build-server' -Recurse -Force }
if (Test-Path 'dist-server') { Remove-Item 'dist-server' -Recurse -Force }

pyinstaller --noconfirm --clean --onefile --name GameVaultServer `
  --distpath dist-server --workpath build-server `
  --add-data 'index.html;.' `
  --add-data 'store.html;.' `
  --add-data 'accounts.html;.' `
  --hidden-import cloud_sync serveur_local.py

New-Item -ItemType Directory -Force -Path 'resources/server' | Out-Null
Copy-Item 'dist-server/GameVaultServer.exe' 'resources/server/GameVaultServer.exe' -Force
Write-Host 'Serveur prêt dans resources/server/GameVaultServer.exe' -ForegroundColor Green
