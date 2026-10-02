@echo off
setlocal
set "ROOT=%~dp0"
where py >nul 2>&1 && (
  py -3 "%ROOT%app\command.py" %*
  exit /b %errorlevel%
)
where python >nul 2>&1 && (
  python "%ROOT%app\command.py" %*
  exit /b %errorlevel%
)
echo EDR needs Python 3.11+. Install: winget install Python.Python.3.11
exit /b 1
