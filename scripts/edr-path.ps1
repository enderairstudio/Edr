# Shared PATH / legacy cleanup for Install-EDR.ps1 and build tooling
function Test-IsEdrPathEntry {
    param([string]$Entry)
    if (-not $Entry) { return $false }
    $u = $Entry.ToUpperInvariant()
    if ($u -match '\\EDR(\\|$)' -or $u -match '\\EDR-SETUP(\\|$)') { return $true }
    if ($u -match '\\NODE_MODULES\\@ENDERAIR\\EDR') { return $true }
    return $false
}

function Remove-LegacyEdrFromUserPath {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { return }
    $parts = $userPath -split ";" | Where-Object { $_ -and -not (Test-IsEdrPathEntry $_) }
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";").Trim(";"), "User")
}

function Add-EdrToFrontOfUserPath {
    param([string]$InstallDir)
    Remove-LegacyEdrFromUserPath
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if ($userPath) {
        $parts = $userPath -split ";" | Where-Object { $_ -and ($_.Trim() -ne $InstallDir) }
    }
    $updated = (@($InstallDir) + $parts) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $updated.Trim(";"), "User")
}

function Remove-NpmLegacyEdr {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { return }
    $null = & npm uninstall -g "@enderair/edr" --loglevel=error 2>&1
}

function Remove-LegacyEdrInstallDirs {
    $targets = @(
        (Join-Path $env:LOCALAPPDATA "EDR"),
        (Join-Path $env:LOCALAPPDATA "EDR\EDR-Setup"),
        (Join-Path $env:APPDATA "EDR")
    )
    foreach ($dir in $targets) {
        if (Test-Path $dir) {
            Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
