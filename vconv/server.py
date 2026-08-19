"""Flask 路由（M1 骨架：首页 + ffmpeg 状态 + 设置；任务相关路由在 M2/M3 加入）。"""
from __future__ import annotations

import os

from flask import jsonify, request

from . import config, ffmpeg_util


def _validate_settings(data: dict):
    """校验并归一化设置，返回 (normalized, errors)。"""
    current = config.load_config()
    errors = []
    out = dict(current)

    if "workers" in data:
        w = data["workers"]
        if isinstance(w, bool) or not isinstance(w, int) or not (config.MIN_WORKERS <= w <= config.MAX_WORKERS):
            errors.append("并发数必须是 {}-{} 的整数".format(config.MIN_WORKERS, config.MAX_WORKERS))
        else:
            out["workers"] = w

    if "default_output_dir" in data:
        d = data["default_output_dir"]
        if not isinstance(d, str):
            errors.append("输出目录必须是字符串")
        else:
            out["default_output_dir"] = d.strip()

    if "ffmpeg_path" in data:
        p = data["ffmpeg_path"]
        if not isinstance(p, str):
            errors.append("ffmpeg 路径必须是字符串")
        elif p.strip() and not os.path.isfile(p.strip()):
            errors.append("指定的 ffmpeg 路径不存在: {}".format(p))
        else:
            out["ffmpeg_path"] = p.strip()

    return out, errors


def register_routes(app) -> None:
    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/ffmpeg")
    def api_ffmpeg_status():
        return jsonify(ffmpeg_util.status())

    @app.get("/api/settings")
    def api_get_settings():
        cfg = config.load_config()
        return jsonify({
            **cfg,
            "workers": config.resolve_workers(cfg),
            "cpu_count": os.cpu_count(),
        })

    @app.put("/api/settings")
    def api_put_settings():
        data = request.get_json(silent=True) or {}
        normalized, errors = _validate_settings(data)
        if errors:
            return jsonify({"error": "；".join(errors)}), 422
        config.save_config(normalized)
        return jsonify({
            **normalized,
            "workers": config.resolve_workers(normalized),
            "cpu_count": os.cpu_count(),
        })
