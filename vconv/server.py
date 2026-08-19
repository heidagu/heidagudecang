"""Flask 路由。"""
from __future__ import annotations

import os

from flask import jsonify, request

from . import config, ffmpeg_util, ffprobe_util, native_dialog
from .models import ConversionSettings


def _validate_settings(data: dict):
    """校验并归一化设置，返回 (normalized, errors)。"""
    current = config.load_config()
    errors = []
    out = dict(current)

    if "workers" in data:
        w = data["workers"]
        if isinstance(w, bool) or not isinstance(w, int) or not (0 <= w <= config.MAX_WORKERS):
            errors.append("并发数必须是 0-{} 的整数（0 = 自动）".format(config.MAX_WORKERS))
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
    engine = app.extensions["engine"]

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    # ---- ffmpeg 状态 ----

    @app.get("/api/ffmpeg")
    def api_ffmpeg_status():
        return jsonify(ffmpeg_util.status())

    @app.post("/api/ffmpeg/download")
    def api_ffmpeg_download():
        if not ffmpeg_util.start_download():
            return jsonify({"error": "下载已在进行中"}), 409
        return jsonify(ffmpeg_util.download_state()), 202

    @app.get("/api/hwaccel")
    def api_hwaccel():
        ff_path, _ = ffmpeg_util.find_binary("ffmpeg")
        if not ff_path:
            return jsonify({"available": {}})
        return jsonify({"available": ffmpeg_util.detect_hw_encoders(ff_path)})

    # ---- 设置 ----

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
        old_workers = config.resolve_workers(config.load_config())
        config.save_config(normalized)
        new_workers = config.resolve_workers(normalized)
        if new_workers != old_workers:
            engine.set_workers(new_workers)
        return jsonify({
            **normalized,
            "workers": new_workers,
            "cpu_count": os.cpu_count(),
        })

    # ---- 任务 ----

    @app.post("/api/jobs")
    def api_submit_jobs():
        data = request.get_json(silent=True) or {}
        inputs = data.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            return jsonify({"error": "请先选择至少一个文件"}), 422
        if not all(isinstance(p, str) and p.strip() for p in inputs):
            return jsonify({"error": "文件路径格式无效"}), 422
        try:
            settings = ConversionSettings.from_dict(data.get("settings") or {})
        except (TypeError, ValueError):
            return jsonify({"error": "转换参数格式无效"}), 422
        try:
            jobs = engine.submit([p.strip() for p in inputs], settings)
        except ValueError as e:
            return jsonify({"error": str(e)}), 422
        return jsonify({"jobs": [j.to_dict() for j in jobs]}), 201

    @app.get("/api/jobs")
    def api_list_jobs():
        return jsonify(engine.list_jobs())

    @app.post("/api/jobs/<job_id>/cancel")
    def api_cancel_job(job_id):
        job = engine.cancel(job_id)
        if not job:
            return jsonify({"error": "任务不存在或已结束"}), 404
        return jsonify(job.to_dict())

    @app.delete("/api/jobs/<job_id>")
    def api_delete_job(job_id):
        err = engine.delete(job_id)
        if err:
            return jsonify({"error": err}), (404 if "不存在" in err else 409)
        return "", 204

    # ---- 文件选择 / 探测 ----

    @app.post("/api/pick-files")
    def api_pick_files():
        data = request.get_json(silent=True) or {}
        try:
            paths = native_dialog.pick_files(folder=bool(data.get("folder")))
        except native_dialog.DialogError as e:
            return jsonify({"error": str(e)}), 422
        return jsonify({"paths": paths, "cancelled": not paths})

    @app.post("/api/probe")
    def api_probe():
        data = request.get_json(silent=True) or {}
        path = (data.get("path") or "").strip()
        if not path or not os.path.isfile(path):
            return jsonify({"error": "文件不存在: {}".format(path)}), 422
        fp_path, _ = ffmpeg_util.find_binary("ffprobe")
        if not fp_path:
            return jsonify({"error": "未检测到 ffprobe"}), 422
        try:
            info = ffprobe_util.probe(fp_path, path)
        except Exception as e:
            return jsonify({"error": str(e)}), 422
        return jsonify(info)
