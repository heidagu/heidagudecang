"""原生文件选择对话框桥接。

浏览器界面拿不到真实文件系统路径，由本机对话框提供：
macOS 用 osascript，Windows 用 PowerShell OpenFileDialog。
"""
from __future__ import annotations

import os
import subprocess

PROMPT = "选择要转换的视频文件"
DIALOG_TIMEOUT = 300    # 对话框等待用户操作的秒数


class DialogError(RuntimeError):
    """对话框调用失败（如系统未安装 osascript / PowerShell）。"""


def pick_files(folder: bool = False) -> list:
    """弹出原生对话框，返回所选路径列表；用户取消返回空列表。"""
    if os.name == "nt":
        return _pick_windows(folder)
    return _pick_macos(folder)


def _pick_macos(folder: bool) -> list:
    if folder:
        script = ('tell application "System Events" to set chosen to '
                  '(choose folder with prompt "选择输出目录")\n'
                  "return POSIX path of chosen")
    else:
        script = ('tell application "System Events" to set chosen to '
                  '(choose file with prompt "{}" with multiple selections allowed)\n'
                  'set out to ""\n'
                  "repeat with f in chosen\n"
                  "set out to out & (POSIX path of f) & linefeed\n"
                  "end repeat\n"
                  "return out").format(PROMPT)
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=DIALOG_TIMEOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DialogError("无法调用系统文件选择对话框（osascript）: {}".format(e))
    if proc.returncode != 0:
        raise DialogError("文件选择失败: {}".format((proc.stderr or "").strip()[:200]))
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _pick_windows(folder: bool) -> list:
    if folder:
        ps = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
              "Add-Type -AssemblyName System.Windows.Forms\n"
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog\n"
              '$d.Description = "选择输出目录"\n'
              "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
              "{ Write-Output $d.SelectedPath }")
    else:
        ps = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
              "Add-Type -AssemblyName System.Windows.Forms\n"
              "$d = New-Object System.Windows.Forms.OpenFileDialog\n"
              "$d.Multiselect = $true\n"
              '$d.Title = "' + PROMPT + '"\n'
              "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
              "{ $d.FileNames | ForEach-Object { Write-Output $_ } }")
    kwargs = {
        "capture_output": True, "timeout": DIALOG_TIMEOUT,
        "text": True, "encoding": "utf-8", "errors": "replace",
    }
    if os.name == "nt":
        from .process_utils import CREATE_NO_WINDOW
        kwargs["creationflags"] = CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps], **kwargs)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DialogError("无法调用系统文件选择对话框（PowerShell）: {}".format(e))
    if proc.returncode != 0:
        raise DialogError("文件选择失败: {}".format((proc.stderr or "").strip()[:200]))
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
