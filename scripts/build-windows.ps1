$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host 'GameVault - build complet Windows 11' -ForegroundColor Cyan
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js 20+ est requis.' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'npm est requis.' }

npm install
npm run build:win
if (-not (Test-Path 'dist-app')) { throw 'Le dossier de sortie dist-app est absent.' }
Write-Host 'Installateurs disponibles dans dist-app/' -ForegroundColor Green
