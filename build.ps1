# Full Windows release build: EDR-Setup.exe + SAC-safe scripts + zips.
# Usage: powershell -File build.ps1
#        powershell -File build.ps1 -BundlePython
#        powershell -File build.ps1 -SkipNpmPublish
param(
    [switch]$BundlePython,
    [switch]$SkipNpmPublish
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$DistEdr = Join-Path $Root "dist\edr"
$InstallerDir = Join-Path $Root "dist\EDR-Setup"
$SetupExe = Join-Path $Root "dist\EDR-Setup.exe"
$AppFiles = @(
    "command.py", "handler.py", "share.py", "print.py", "error.py", "relay.py", "guard.py",
    "watch.py", "qrterm.py", "doctor_checks.py"
)

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

function Expand-ZipFile {
    param([string]$ZipPath, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $Destination)
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

function Install-QrVendor($AppDir) {
    $vendor = Join-Path $Root ("build\qr_vendor_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $vendor | Out-Null
    $py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { $null }
    if (-not $py) {
        Write-Host "Python not found; skipping bundled qrcode (terminal QR may be unavailable)." -ForegroundColor Yellow
        return
    }
    $pipOut = Join-Path $vendor "pip.out"
    $pipErr = Join-Path $vendor "pip.err"
    $pip = Start-Process -FilePath $py `
        -ArgumentList @("-m", "pip", "download", "qrcode", "--only-binary=:all:", "--no-deps", "-d", $vendor, "-q", "--disable-pip-version-check") `
        -Wait -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $pipOut `
        -RedirectStandardError $pipErr
    if ($pip.ExitCode -ne 0) {
        Write-Host "pip download qrcode failed; terminal QR may be unavailable." -ForegroundColor Yellow
        return
    }
    $wheel = Get-ChildItem $vendor -Filter "qrcode-*.whl" | Select-Object -First 1
    if ($wheel) {
        $extract = Join-Path $vendor "wheel"
        New-Item -ItemType Directory -Force -Path $extract | Out-Null
        Expand-ZipFile $wheel.FullName $extract
        $pkg = Join-Path $extract "qrcode"
        if (Test-Path $pkg) {
            Copy-Item -Recurse $pkg (Join-Path $AppDir "qrcode") -Force
        }
    }
}

function Copy-App($OutDir) {
    New-Item -ItemType Directory -Force -Path "$OutDir\app" | Out-Null
    foreach ($file in $AppFiles) {
        Copy-Item (Join-Path $Root $file) (Join-Path "$OutDir\app" $file) -Force
    }
    Install-QrVendor (Join-Path $OutDir "app")
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

function Get-EdrVersion {
    $match = Select-String -Path (Join-Path $Root "print.py") -Pattern '^VERSION = "([^"]+)"' | Select-Object -First 1
    if (-not $match) { throw "Could not read VERSION from print.py" }
    return $match.Matches[0].Groups[1].Value
}

function Sync-NpmPackageVersion {
    param([string]$Version)

    $packagePath = Join-Path $Root "package.json"
    if (-not (Test-Path $packagePath)) {
        Write-Host "package.json not found; skipping npm package sync." -ForegroundColor Yellow
        return
    }

    $package = Get-Content $packagePath -Raw | ConvertFrom-Json
    if ($package.version -ne $Version) {
        Write-Host "Updating package.json version to $Version..." -ForegroundColor Cyan
        $package.version = $Version
        $package | ConvertTo-Json -Depth 10 | Set-Content -Path $packagePath -Encoding UTF8
    }
}

function Get-NpmPackageName {
    $packagePath = Join-Path $Root "package.json"
    if (-not (Test-Path $packagePath)) {
        return $null
    }
    $package = Get-Content $packagePath -Raw | ConvertFrom-Json
    return $package.name
}

function Test-NpmLoggedIn {
    & npm whoami --prefer-online --silent 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Publish-NpmPackage {
    param([string]$Version)

    if ($SkipNpmPublish -or $env:EDR_SKIP_NPM_PUBLISH) {
        Write-Host "Skipping npm publish." -ForegroundColor Yellow
        return
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host "npm not found; skipping npm publish." -ForegroundColor Yellow
        return
    }

    $PackageName = Get-NpmPackageName
    if (-not $PackageName) {
        Write-Host "package.json has no package name; skipping npm publish." -ForegroundColor Yellow
        return
    }

    if (-not (Test-NpmLoggedIn)) {
        Write-Host "npm is not logged in; skipping npm publish." -ForegroundColor Yellow
        Write-Host "Run: npm login" -ForegroundColor Yellow
        Write-Host "Then make sure '$PackageName' is under an npm account or scope you own." -ForegroundColor Yellow
        return
    }

    Write-Host "Checking npm package $PackageName@$Version..." -ForegroundColor Cyan
    & npm view "$PackageName@$Version" version --silent 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "$PackageName@$Version is already published. Bump VERSION in print.py to publish a new npm update." -ForegroundColor Yellow
        return
    }

    Write-Host "Verifying npm package contents..." -ForegroundColor Cyan
    & npm pack --dry-run
    if ($LASTEXITCODE -ne 0) {
        Write-Host "npm pack failed; skipping npm publish." -ForegroundColor Yellow
        return
    }

    Write-Host "Publishing $PackageName@$Version to npm..." -ForegroundColor Cyan
    & npm publish --access public
    if ($LASTEXITCODE -ne 0) {
        Write-Host "npm publish failed, but the Windows build is complete." -ForegroundColor Yellow
        Write-Host "For a 404 on $PackageName, publish with an npm account that owns that package scope, create the npm org/scope, or rename package.json to a scope you own." -ForegroundColor Yellow
        Write-Host "You can also build without publishing: powershell -File build.ps1 -SkipNpmPublish" -ForegroundColor Yellow
        return
    }

    Write-Host "Published $PackageName@$Version to npm." -ForegroundColor Green
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
    $smokeOk = $false
    try {
        & "$DistEdr\edr.exe" version 2>$null
        $smokeOk = $LASTEXITCODE -eq 0
    } catch {
        $smokeOk = $false
    }
    if (-not $smokeOk) {
        Write-Host "edr.exe smoke test blocked or failed; using Python..." -ForegroundColor Yellow
        $py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
        & $py "$DistEdr\app\command.py" version
        if ($LASTEXITCODE -ne 0) { throw "EDR CLI smoke test failed" }
    }
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

$Version = Get-EdrVersion
Sync-NpmPackageVersion -Version $Version
Publish-NpmPackage -Version $Version

Write-Host ""
Write-Host "Windows release:" -ForegroundColor Green
Write-Host "  $SetupExe" -ForegroundColor White
Write-Host "  $InstallerDir\" -ForegroundColor Gray
Write-Host "  $zipInstall" -ForegroundColor Gray
Write-Host ""
Write-Host "If Smart App Control blocks the EXE, use START-HERE.cmd in the folder above." -ForegroundColor Yellow
Pop-Location
