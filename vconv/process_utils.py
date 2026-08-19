"""子进程生成与进程树终止的跨平台封装。"""
from __future__ import annotations

import os
import signal
import subprocess
import time

if os.name == "nt":
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000     # 打包为 --windowed 后防止弹黑窗
else:
    CREATE_NEW_PROCESS_GROUP = 0
    CREATE_NO_WINDOW = 0


def spawn_kwargs() -> dict:
    """ffmpeg 子进程的生成参数：独立进程组（POSIX）/ 无窗口（Windows）。"""
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": 0,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return kwargs


def terminate_tree(proc: subprocess.Popen, grace: float = 3.0) -> None:
    """终止整个进程树：先优雅 SIGTERM，超时 SIGKILL；Windows 用 taskkill /T /F。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
