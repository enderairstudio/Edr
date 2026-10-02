@echo off
setlocal
cd /d "%~dp0"
echo EDR local Windows build
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build\windows-release.ps1" -SkipNpmPublish
set "BUILD_EXIT=%ERRORLEVEL%"
if not "%BUILD_EXIT%"=="0" echo Build failed with exit code %BUILD_EXIT%.
if "%BUILD_EXIT%"=="0" echo Build complete. See the dist folder.
echo.
pause
exit /b %BUILD_EXIT%
