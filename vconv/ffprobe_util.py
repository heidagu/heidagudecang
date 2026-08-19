"""ffprobe 探测工具。"""
from __future__ import annotations

import json
import subprocess

PROBE_TIMEOUT = 30

EMPTY_INFO = {
    "duration_ms": 0, "width": 0, "height": 0, "fps": 0.0,
    "vcodec": "", "acodec": "", "has_video": False, "has_audio": False,
}


def _parse_rate(rate: str) -> float:
    """'30000/1001' 或 '30' → float；失败返回 0。"""
    if not rate:
        return 0.0
    try:
        if "/" in rate:
            num, _, den = rate.partition("/")
            return float(num) / float(den)
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0


def parse_probe_json(text: str) -> dict:
    """解析 ffprobe -print_format json 输出（纯函数，可单测）。"""
    result = dict(EMPTY_INFO)
    data = json.loads(text or "{}")
    fmt = data.get("format") or {}
    try:
        result["duration_ms"] = int(float(fmt.get("duration", 0)) * 1000)
    except (ValueError, TypeError):
        result["duration_ms"] = 0
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and not result["has_video"]:
            result["has_video"] = True
            result["vcodec"] = stream.get("codec_name", "")
            result["width"] = int(stream.get("width") or 0)
            result["height"] = int(stream.get("height") or 0)
            result["fps"] = _parse_rate(
                stream.get("r_frame_rate") or stream.get("avg_frame_rate") or "")
        elif stream.get("codec_type") == "audio" and not result["has_audio"]:
            result["has_audio"] = True
            result["acodec"] = stream.get("codec_name", "")
    return result


def probe(ffprobe_path: str, input_path: str) -> dict:
    """探测文件，返回 duration_ms/width/height/fps/vcodec/acodec/has_video/has_audio。"""
    proc = subprocess.run(
        [ffprobe_path, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", input_path],
        capture_output=True, timeout=PROBE_TIMEOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError("ffprobe 失败: {}".format((proc.stderr or "").strip()[:300]))
    return parse_probe_json(proc.stdout or "")
