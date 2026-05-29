$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& python (Join-Path $ScriptDir "command.py") @args
exit $LASTEXITCODE
