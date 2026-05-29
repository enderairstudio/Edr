# EDR install without EDR-Setup.exe (for Smart App Control / SmartScreen).
# Run: powershell -ExecutionPolicy Bypass -File Install-EDR.ps1
$ErrorActionPreference = "Stop"

$InstallDir = Join-Path $env:LOCALAPPDATA "EDR"
$SourceDir = Join-Path $PSScriptRoot "edr"
. (Join-Path $PSScriptRoot "edr-path.ps1")

function Clear-MarkOfTheWeb {
    param([string]$Root)
    Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
        try { Unblock-File -LiteralPath $_.FullName -ErrorAction SilentlyContinue } catch {}
    }
}

function Install-EdrPayload {
    param([string]$From, [string]$To)
    if (-not (Test-Path $From)) {
        throw "Missing folder: $From`nExtract EDR-Install.zip fully, then run this script again."
    }
    if (Test-Path $To) { Remove-Item -Recurse -Force $To }
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    Copy-Item -Path (Join-Path $From "*") -Destination $To -Recurse -Force
}

Write-Host ""
Write-Host "  EDR Project Sharer - Setup" -ForegroundColor Cyan
Write-Host "  (no setup.exe - works with Smart App Control)" -ForegroundColor DarkGray
Write-Host ""

Clear-MarkOfTheWeb -Root $PSScriptRoot
Write-Host "Removing old npm EDR (if present)..." -ForegroundColor DarkGray
Remove-NpmLegacyEdr
Write-Host "Removing previous EDR install..." -ForegroundColor DarkGray
Remove-LegacyEdrInstallDirs
Install-EdrPayload -From $SourceDir -To $InstallDir
Clear-MarkOfTheWeb -Root $InstallDir
Add-EdrToFrontOfUserPath -InstallDir $InstallDir

Write-Host "Installed to: $InstallDir" -ForegroundColor Green
Write-Host ""
Write-Host "Open a NEW terminal and run:" -ForegroundColor White
Write-Host "  edr version" -ForegroundColor Yellow
Write-Host "  edr doctor" -ForegroundColor Yellow
Write-Host ""
Write-Host "If PowerShell still shows old help, close ALL terminals and try again." -ForegroundColor DarkGray
Write-Host ""
