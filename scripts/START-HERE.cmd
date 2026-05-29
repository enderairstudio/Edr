@echo off
title EDR Project Sharer Setup
cd /d "%~dp0"
echo.
echo  EDR Setup - Smart App Control safe install
echo  (does not run EDR-Setup.exe)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-EDR.ps1"
if errorlevel 1 goto failed
echo.
echo  Done. Press any key to close.
pause >nul
exit /b 0

:failed
echo.
echo  Install failed.
pause
exit /b 1
