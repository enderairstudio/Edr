# EDR CLI shim — PowerShell must use this instead of npm @enderair/edr
$EdrDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$EdrDir\edr.exe" @args
exit $LASTEXITCODE
