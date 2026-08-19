@echo off
rem VConv Windows 一键打包：双击运行即可（内部调用 build_windows.ps1）
rem 要求：已安装 Python 3.9+ 并勾选 "Add Python to PATH"
chcp 65001 >nul
cd /d "%~dp0.."
echo ============================================
echo  VConv Windows 打包脚本
echo ============================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1"
if errorlevel 1 (
  echo.
  echo 打包失败，请检查上方错误信息
) else (
  echo.
  echo 打包完成，产物在仓库根目录（VConv-windows-x64-*.zip）
)
echo.
pause
