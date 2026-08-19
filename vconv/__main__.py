"""VConv 启动入口：python -m vconv 或 python run.py。"""
from __future__ import annotations

import argparse
import webbrowser
from threading import Timer

from . import __version__, create_app


def _open_browser(url: str) -> None:
    Timer(0.8, lambda: webbrowser.open(url)).start()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="vconv",
        description="VConv — 基于 ffmpeg 的视频格式转换工具（本地 Web 界面）",
    )
    parser.add_argument("--port", type=int, default=None,
                        help="监听端口（默认 8756，被占用时自动顺延 +1..+5）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--version", action="version", version="vconv {}".format(__version__))
    args = parser.parse_args(argv)

    app = create_app(port=args.port)
    host, port = app.config["VCONV_HOST"], app.config["VCONV_PORT"]
    url = "http://{}:{}/".format(host, port)
    if not args.no_browser:
        _open_browser(url)
    print("VConv 已启动: {}".format(url))
    print("按 Ctrl+C 退出")
    app.run(host=host, port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
