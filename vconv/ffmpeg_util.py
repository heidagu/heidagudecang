"""ffmpeg / ffprobe 检测与获取工具。"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

from . import config

# 静态构建下载地址（2026-08 已用 HEAD 请求验证可用）
# Windows: gyan.dev LGPL essentials 构建
# macOS: 优先 martin-riedl.de 原生 arm64；Intel / 回退用 evermeet.cx
FFMPEG_DOWNLOAD_URLS = {
    "win32": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "darwin": (
        "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip"
        if platform.machine() == "arm64"
        else "https://evermeet.cx/ffmpeg/getrelease/zip"
    ),
}

PROBE_TIMEOUT = 15  # 秒


def exe_name(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def _candidate_dirs():
    """按优先级返回可能包含 ffmpeg 二进制的目录。"""
    dirs = []
    base = getattr(sys, "_MEIPASS", None)  # PyInstaller 解包目录
    if base:
        dirs.append(os.path.join(base, "ffmpeg"))
    dirs.append(config.ffmpeg_cache_dir())  # 一键下载缓存目录
    return dirs


def find_binary(kind: str):
    """查找 ffmpeg / ffprobe，返回 (path, source)；source ∈ bundled|config|path。

    检测顺序：打包自带 → 下载缓存 → config.json 覆盖 → 系统 PATH。
    """
    name = exe_name(kind)
    for d in _candidate_dirs():
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p, "bundled"
    explicit = config.load_config().get("ffmpeg_path", "")
    if explicit:
        p = explicit if kind == "ffmpeg" else os.path.join(os.path.dirname(explicit), name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p, "config"
    p = shutil.which(name)
    if p:
        return p, "path"
    return None, None


def get_version(ffmpeg_path: str):
    """运行 ffmpeg -version，返回第一行版本字符串；失败返回 None。"""
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            timeout=PROBE_TIMEOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    lines = (proc.stdout or "").splitlines()
    return lines[0].strip() if lines else None


def status() -> dict:
    """检测结果汇总，供 GET /api/ffmpeg 使用。"""
    ff_path, ff_src = find_binary("ffmpeg")
    fp_path, _ = find_binary("ffprobe")
    if not ff_path or not fp_path:
        return {
            "status": "missing",
            "path": None,
            "version": None,
            "source": None,
            "download_url": FFMPEG_DOWNLOAD_URLS.get(sys.platform),
        }
    return {
        "status": "ok",
        "path": ff_path,
        "version": get_version(ff_path),
        "source": ff_src,
    }
