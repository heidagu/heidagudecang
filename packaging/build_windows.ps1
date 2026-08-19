# VConv Windows build script (run via build_windows.bat, or call directly)
# Requires: Python 3.9+ on PATH, internet access for pip
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".venv-build")) { python -m venv .venv-build }
& ".venv-build\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& ".venv-build\Scripts\python.exe" -m pip install -r requirements-dev.txt

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& ".venv-build\Scripts\pyinstaller.exe" --noconfirm packaging\vconv.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed (exit $LASTEXITCODE)" }

$ver = & ".venv-build\Scripts\python.exe" -c "from vconv import __version__; print(__version__)"
$out = "VConv-windows-x64-v$ver.zip"
Remove-Item $out -ErrorAction SilentlyContinue
Compress-Archive -Path "dist\VConv\*" -DestinationPath $out -Force
Write-Host "Output: $out"
