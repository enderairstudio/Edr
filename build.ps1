# Full Windows release build: EDR-Setup.exe + SAC-safe scripts + zips.
# Usage: powershell -File build.ps1
#        powershell -File build.ps1 -BundlePython
param([switch]$BundlePython)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$DistEdr = Join-Path $Root "dist\edr"
$InstallerDir = Join-Path $Root "dist\EDR-Setup"
$SetupExe = Join-Path $Root "dist\EDR-Setup.exe"
$AppFiles = @("command.py", "handler.py", "share.py", "print.py", "error.py", "relay.py", "guard.py")

function Get-Iscc {
    @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Get-Csc {
    @(
        "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
        "$env:SystemRoot\Microsoft.NET\Framework\v4.0.30319\csc.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Remove-Tree {
    param([string[]]$Paths)
    foreach ($p in $Paths) {
        if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Build-Launcher($OutDir) {
    $Csc = Get-Csc
    if (-not $Csc) { throw "csc.exe not found (.NET Framework required)" }
    $iconArg = if (Test-Path "$Root\icon.ico") { "/win32icon:$Root\icon.ico" } else { "" }
    & $Csc /nologo /target:exe /optimize+ /out:"$OutDir\edr.exe" $iconArg `
        "/win32manifest:$Root\launcher\app.manifest" `
        "$Root\launcher\AssemblyInfo.cs" "$Root\launcher\EdrLauncher.cs"
    if ($LASTEXITCODE -ne 0) { throw "Launcher compile failed" }
}

function Copy-App($OutDir) {
    New-Item -ItemType Directory -Force -Path "$OutDir\app" | Out-Null
    foreach ($file in $AppFiles) {
        Copy-Item (Join-Path $Root $file) (Join-Path "$OutDir\app" $file) -Force
    }
    Copy-Item "$Root\scripts\edr.cmd" (Join-Path $OutDir "edr.cmd") -Force
    Copy-Item "$Root\scripts\edr.ps1" (Join-Path $OutDir "edr.ps1") -Force
    Get-ChildItem "$OutDir\app\__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
}

function Publish-ReleaseFolder {
    param([string]$TargetDir, [string]$PayloadDir, [string]$InstallerExePath)

    Remove-Tree @($TargetDir)
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    Copy-Item -Recurse $PayloadDir (Join-Path $TargetDir "edr")
    Copy-Item "$Root\scripts\START-HERE.cmd" $TargetDir
    Copy-Item "$Root\scripts\Install-EDR.ps1" $TargetDir
    Copy-Item "$Root\scripts\edr-path.ps1" $TargetDir
    Copy-Item "$Root\installer\INSTALL.txt" $TargetDir
    Copy-Item "$Root\installer\SAC-READ-ME-FIRST.txt" $TargetDir
    if ($InstallerExePath -and (Test-Path $InstallerExePath)) {
        Copy-Item $InstallerExePath (Join-Path $TargetDir "EDR-Setup.exe")
    }
}

Push-Location $Root

Write-Host "Removing build/ and dist/..." -ForegroundColor Cyan
Remove-Tree @(
    (Join-Path $Root "build"),
    (Join-Path $Root "dist")
)
New-Item -ItemType Directory -Force -Path (Join-Path $Root "dist") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "build\cache") -ErrorAction SilentlyContinue | Out-Null

Write-Host "Building EDR CLI..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $DistEdr | Out-Null
Copy-App $DistEdr
Build-Launcher $DistEdr

if ((Get-Command py -ErrorAction SilentlyContinue) -or (Get-Command python -ErrorAction SilentlyContinue)) {
    & "$DistEdr\edr.exe" version
    if ($LASTEXITCODE -ne 0) { throw "edr.exe smoke test failed" }
    Get-ChildItem "$DistEdr\app\__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
}

Write-Host "Building EDR-Setup.exe (Inno Setup)..." -ForegroundColor Cyan
$Iscc = Get-Iscc
if (-not $Iscc) {
    throw "Inno Setup 6 required. Install: winget install JRSoftware.InnoSetup"
}
& $Iscc "$Root\installer\EDR-Setup.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno compile failed" }

$InnoOutput = Join-Path $InstallerDir "EDR-Setup.exe"
if (-not (Test-Path $InnoOutput)) {
    throw "Missing installer output: $InnoOutput"
}

Copy-Item $InnoOutput $SetupExe -Force
$Mt = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter "mt.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "\\x64\\mt\.exe$" } | Select-Object -First 1
if ($Mt) {
    & $Mt.FullName -nologo -manifest "$Root\installer\setup.manifest" "-outputresource:$SetupExe;1" | Out-Null
    Copy-Item $SetupExe $InnoOutput -Force
}

Publish-ReleaseFolder -TargetDir $InstallerDir -PayloadDir $DistEdr -InstallerExePath $SetupExe

$zipInstall = Join-Path $Root "dist\EDR-Install.zip"
$zipPortable = Join-Path $Root "dist\EDR-win64.zip"
if (Test-Path $zipInstall) { Remove-Item -Force $zipInstall }
if (Test-Path $zipPortable) { Remove-Item -Force $zipPortable }
Compress-Archive -Path (Join-Path $InstallerDir "*") -DestinationPath $zipInstall -Force
Compress-Archive -Path $DistEdr -DestinationPath $zipPortable -Force

if ($env:EDR_SIGN_PFX -and (Test-Path $env:EDR_SIGN_PFX)) {
    $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($signtool) {
        Write-Host "Code signing..." -ForegroundColor Cyan
        $pass = if ($env:EDR_SIGN_PASSWORD) { "/p:$($env:EDR_SIGN_PASSWORD)" } else { "" }
        foreach ($target in @($SetupExe, "$DistEdr\edr.exe")) {
            & $signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $pass `
                "/f:$($env:EDR_SIGN_PFX)" $target
        }
    }
}

if ($BundlePython) {
    Write-Host "BundlePython not wired in this build script yet." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Windows release:" -ForegroundColor Green
Write-Host "  $SetupExe" -ForegroundColor White
Write-Host "  $InstallerDir\" -ForegroundColor Gray
Write-Host "  $zipInstall" -ForegroundColor Gray
Write-Host ""
Write-Host "If Smart App Control blocks the EXE, use START-HERE.cmd in the folder above." -ForegroundColor Yellow
Pop-Location
