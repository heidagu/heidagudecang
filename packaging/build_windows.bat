@echo off
rem VConv Windows one-click build (double-click to run; calls build_windows.ps1)
rem Requires: Python 3.9+ installed with "Add Python to PATH" checked
cd /d "%~dp0.."
echo ============================================
echo  VConv Windows build script
echo ============================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1"
if errorlevel 1 (
  echo.
  echo Build FAILED. See error messages above.
) else (
  echo.
  echo Build OK. Output zip is in the repo root: VConv-windows-x64-*.zip
)
echo.
pause
