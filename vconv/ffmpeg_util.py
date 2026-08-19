"""ffmpeg / ffprobe 检测、下载与硬件编码器探测。"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile

from . import config

# 静态构建下载地址（2026-08 已用 HEAD 请求验证可用）
# Windows: gyan.dev LGPL essentials 构建（单 zip，bin/ 子目录含 ffmpeg.exe + ffprobe.exe）
# macOS: 优先 martin-riedl.de 原生 arm64；Intel / 回退用 evermeet.cx
#   martin-riedl 的 zip 每个只含一个二进制，因此 ffmpeg 与 ffprobe 分开下载
FFMPEG_DOWNLOAD_URLS = {
    "win32": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "darwin": (
        "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip"
        if platform.machine() == "arm64"
        else "https://evermeet.cx/ffmpeg/getrelease/zip"
    ),
}

# 一键下载的包清单：每个包一个 zip，bins 为该 zip 内必须包含的二进制
DOWNLOAD_PACKAGES = {
    "win32": [
        {"url": FFMPEG_DOWNLOAD_URLS["win32"], "bins": ["ffmpeg.exe", "ffprobe.exe"]},
    ],
    "darwin": [
        {"url": FFMPEG_DOWNLOAD_URLS["darwin"], "bins": ["ffmpeg"]},
        {"url": (
            "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffprobe.zip"
            if platform.machine() == "arm64"
            else "https://evermeet.cx/ffprobe/getrelease/zip"
        ), "bins": ["ffprobe"]},
    ],
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
            "downloading": _manager.state(),
        }
    return {
        "status": "ok",
        "path": ff_path,
        "version": get_version(ff_path),
        "source": ff_src,
        "downloading": _manager.state(),
    }


# ---- 硬件编码器探测 ----

HW_ENCODER_CANDIDATES = {
    "h264": ["h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"],
    "h265": ["hevc_videotoolbox", "hevc_nvenc", "hevc_qsv", "hevc_amf"],
}

_hw_lock = threading.Lock()
_hw_cache = {"path": None, "data": None}


def detect_hw_encoders(ffmpeg_path: str) -> dict:
    """运行 ffmpeg -encoders，返回 {codec: 可用的硬件编码器名}。按路径缓存。"""
    with _hw_lock:
        if _hw_cache["path"] == ffmpeg_path and _hw_cache["data"] is not None:
            return _hw_cache["data"]
        available = set()
        try:
            proc = subprocess.run(
                [ffmpeg_path, "-hide_banner", "-encoders"],
                capture_output=True, timeout=PROBE_TIMEOUT,
                text=True, encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc and proc.returncode == 0:
            for line in (proc.stdout or "").splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    available.add(parts[1])
        data = {
            codec: enc
            for codec, candidates in HW_ENCODER_CANDIDATES.items()
            for enc in candidates if enc in available
        }
        _hw_cache["path"] = ffmpeg_path
        _hw_cache["data"] = data
        return data


# ---- 一键下载 ----

_dl_lock = threading.Lock()


def _download_file(url: str, dest: str, progress_cb=None) -> None:
    """流式下载到 dest；progress_cb(percent:int 0-99, stage:str)。"""
    req = urllib.request.Request(url, headers={"User-Agent": "VConv/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(min(99, int(done * 100 / total)), "下载中")


def _locate_in(directory: str, name: str):
    """在解压目录中查找指定文件名（gyan.dev 的 zip 带 bin/ 子目录）。"""
    for root, _dirs, files in os.walk(directory):
        for fname in files:
            if fname.lower() == name.lower():
                return os.path.join(root, fname)
    return None


def download_ffmpeg(progress_cb=None) -> str:
    """一键下载静态构建到缓存目录，返回 ffmpeg 路径。

    progress_cb(percent:int 0-100, stage:str)；失败抛异常（含中文消息）。
    """
    packages = DOWNLOAD_PACKAGES.get(sys.platform)
    if not packages:
        raise RuntimeError("当前平台不支持一键下载，请手动安装 ffmpeg")
    with _dl_lock:
        cache_dir = config.ffmpeg_cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(prefix="vconv_dl_")
        try:
            total = len(packages)
            for idx, pkg in enumerate(packages):
                base = idx * 100.0 / total
                span = 100.0 / total

                def pkg_cb(percent: int, stage: str) -> None:
                    if progress_cb:
                        progress_cb(int(base + percent * span / 100.0), stage)

                zip_path = os.path.join(tmp_dir, "pkg{}.zip".format(idx))
                _download_file(pkg["url"], zip_path, pkg_cb)
                extract_dir = os.path.join(tmp_dir, "pkg{}".format(idx))
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(extract_dir)
                for bin_name in pkg["bins"]:
                    src = _locate_in(extract_dir, bin_name)
                    if not src:
                        raise RuntimeError("压缩包中未找到 {}，请手动安装 ffmpeg".format(bin_name))
                    dst = os.path.join(cache_dir, bin_name)
                    shutil.copyfile(src, dst)
                    os.chmod(dst, 0o755)
            ff_path = os.path.join(cache_dir, exe_name("ffmpeg"))
            if not get_version(ff_path):
                raise RuntimeError("下载的 ffmpeg 无法运行，请手动安装")
            return ff_path
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---- 下载管理器（并发门 + 进度状态，供 UI 轮询） ----


class DownloadManager:
    """一键下载的并发门与进度状态。状态字典始终可序列化。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False
        self._percent = 0
        self._stage = ""
        self._error = ""
        self._finished = False    # 最近一次下载已结束（成功或失败）

    def state(self) -> dict:
        with self._lock:
            return {
                "active": self._active,
                "percent": self._percent,
                "stage": self._stage,
                "error": self._error,
                "finished": self._finished,
            }

    def start(self) -> bool:
        """启动后台下载；已在下载中返回 False。"""
        with self._lock:
            if self._active:
                return False
            self._active = True
            self._percent = 0
            self._stage = "准备下载"
            self._error = ""
            self._finished = False

        def run() -> None:
            try:
                download_ffmpeg(self._on_progress)
            except Exception as e:      # 下载/解压/校验失败：记录原因供 UI 展示
                with self._lock:
                    self._error = str(e)
            finally:
                with self._lock:
                    self._finished = True
                    self._active = False

        threading.Thread(target=run, daemon=True, name="vconv-download").start()
        return True

    def _on_progress(self, percent: int, stage: str) -> None:
        with self._lock:
            self._percent = percent
            self._stage = stage


_manager = DownloadManager()


def start_download() -> bool:
    return _manager.start()


def download_state() -> dict:
    return _manager.state()
