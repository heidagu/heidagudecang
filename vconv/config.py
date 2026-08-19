"""跨平台配置与应用数据目录管理。"""
from __future__ import annotations

import json
import os
import sys

APP_NAME = "vconv"
DEFAULT_PORT = 8756

# 允许的并发 worker 数范围
MIN_WORKERS = 1
MAX_WORKERS = 32


def app_data_dir() -> str:
    """应用数据目录：macOS ~/Library/Application Support/vconv；Windows %LOCALAPPDATA%\\vconv。"""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_NAME)


def ffmpeg_cache_dir() -> str:
    """一键下载的 ffmpeg 缓存目录。"""
    return os.path.join(app_data_dir(), "ffmpeg")


def log_file() -> str:
    return os.path.join(app_data_dir(), "vconv.log")


def config_path() -> str:
    return os.path.join(app_data_dir(), "config.json")


DEFAULT_CONFIG = {
    "workers": 0,                # 0 = 自动（CPU 核心数 - 1）
    "default_output_dir": "",    # 空 = 与源文件同目录
    "ffmpeg_path": "",           # 用户手动指定的 ffmpeg 可执行文件路径
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in DEFAULT_CONFIG:
                if key in data:
                    cfg[key] = data[key]
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> str:
    """原子写入 config.json，返回路径。"""
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def resolve_workers(cfg: dict) -> int:
    """计算实际并发数：0 表示自动（CPU 核心数 - 1）。"""
    workers = cfg.get("workers") or 0
    if workers <= 0:
        workers = max(1, (os.cpu_count() or 2) - 1)
    return min(max(int(workers), MIN_WORKERS), MAX_WORKERS)
