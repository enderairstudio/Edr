# Optional Tkinter installer (PyInstaller) — often 3+/71 on VirusTotal. Not for releases.
# Use: powershell -File build.ps1  for the clean Inno installer instead.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Out = Join-Path $Root "dist\EDR-Setup-GUI"

if (-not (Test-Path "$Root\dist\edr")) {
    Write-Host "Run build.ps1 first to create dist\edr" -ForegroundColor Red
    exit 1
}

if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
pyinstaller "$Root\EDR-Setup.spec" --distpath "$Root\dist" --workpath "$Root\build\EDR-Setup-GUI" -y
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Rename-Item (Join-Path $Root "dist\EDR-Setup") $Out -ErrorAction SilentlyContinue
Write-Host "GUI dev build: $Out\EDR-Setup.exe (may flag AV — do not ship)" -ForegroundColor Yellow
