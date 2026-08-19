"""VConv — 基于 ffmpeg 的视频格式转换工具（本地 Web 界面）。"""
from __future__ import annotations

__version__ = "0.1.0"

import logging
import os
import socket
from logging.handlers import RotatingFileHandler

from flask import Flask

from . import config


def _find_free_port(start: int, tries: int = 6) -> int:
    """从 start 开始找第一个可用的 TCP 端口（默认 8756，被占用自动顺延）。"""
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("端口 {}-{} 均被占用，无法启动".format(start, start + tries - 1))


def _setup_logging() -> None:
    """日志同时输出到控制台与 appdata 下的滚动文件（打包后无控制台时依赖文件日志）。"""
    log_dir = config.app_data_dir()
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        handler = RotatingFileHandler(
            os.path.join(log_dir, "vconv.log"),
            maxBytes=1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(logging.StreamHandler())


def create_app(port: int = None) -> Flask:
    """创建 Flask 应用（工厂函数，便于测试）。"""
    _setup_logging()
    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
        static_url_path="/static",
    )
    app.config["VCONV_HOST"] = "127.0.0.1"
    app.config["VCONV_PORT"] = _find_free_port(port or config.DEFAULT_PORT)

    from . import engine as engine_module
    from . import server

    app.extensions["engine"] = engine_module.Engine()
    server.register_routes(app)
    return app
