"""纯函数：把转换设置构建成 ffmpeg 命令行（不含 I/O，便于单测）。"""
from __future__ import annotations

import os
import tempfile
from typing import List, Optional

from .models import AUDIO_EXTRACT_EXTS, SOFTWARE_ENCODERS, ConversionSettings

# 硬件编码器家族 → 画质预设参数（HW 编码器不支持 CRF，用各自的质量参数）
HW_QUALITY_ARGS = {
    "videotoolbox": {"high": ["-q:v", "60"], "medium": ["-q:v", "70"], "low": ["-q:v", "80"]},
    "nvenc": {"high": ["-cq", "18"], "medium": ["-cq", "23"], "low": ["-cq", "28"]},
    "qsv": {"high": ["-global_quality", "20"], "medium": ["-global_quality", "28"], "low": ["-global_quality", "36"]},
    # AMF 的质量参数在不同驱动版本上不一致，用码率兜底
    "amf": {"high": ["-b:v", "12M"], "medium": ["-b:v", "6M"], "low": ["-b:v", "3M"]},
}

AUDIO_ENCODERS = {"aac": "aac", "opus": "libopus", "mp3": "libmp3lame"}


def hw_family(encoder: str) -> str:
    """从编码器名推断家族：h264_videotoolbox → videotoolbox。"""
    for family in ("videotoolbox", "nvenc", "qsv", "amf"):
        if encoder.endswith(family):
            return family
    return ""


def build_vf(settings: ConversionSettings) -> str:
    """构建 -vf 滤镜图（scale + fps），无滤镜时返回空串。"""
    filters = []
    if settings.resolution == "4k":
        filters.append("scale=-2:2160")
    elif settings.resolution == "1080p":
        filters.append("scale=-2:1080")
    elif settings.resolution == "720p":
        filters.append("scale=-2:720")
    elif settings.resolution == "custom":
        filters.append("scale={}:{}".format(settings.custom_width, settings.custom_height))

    if settings.frame_rate == "23.976":
        filters.append("fps=24000/1001")          # 精确有理数，避免漂移
    elif settings.frame_rate == "custom":
        filters.append("fps={:g}".format(float(settings.custom_fps)))
    elif settings.frame_rate != "source":
        filters.append("fps={}".format(settings.frame_rate))
    return ",".join(filters)


def _video_args(settings: ConversionSettings, hw_encoder: Optional[str]) -> List[str]:
    codec = settings.video_codec
    if codec == "copy":
        return ["-c:v", "copy"]
    if settings.hw_accel and hw_encoder:
        family = hw_family(hw_encoder)
        quality = HW_QUALITY_ARGS.get(family, {}).get(settings.hw_quality, ["-b:v", "6M"])
        return ["-c:v", hw_encoder] + quality
    args = ["-c:v", SOFTWARE_ENCODERS[codec]]
    if settings.quality_mode == "crf":
        args += ["-crf", str(int(settings.crf))]
        if codec == "vp9":
            args += ["-b:v", "0"]      # vp9 CRF 模式必须显式 -b:v 0
    else:
        args += ["-b:v", settings.bitrate]
    if codec in ("h264", "h265"):
        args += ["-pix_fmt", "yuv420p"]   # 播放器兼容
    return args


def _audio_args(settings: ConversionSettings) -> List[str]:
    mode = settings.audio_mode
    if mode == "copy":
        return ["-c:a", "copy"]
    if mode == "none":
        return ["-an"]
    if mode == "extract":
        encoder = AUDIO_EXTRACT_EXTS[settings.audio_extract_ext]
        return ["-vn", "-c:a", encoder, "-b:a", settings.audio_bitrate]
    return ["-c:a", AUDIO_ENCODERS[mode], "-b:a", settings.audio_bitrate]


def devnull_path() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"


def build_commands(input_path: str, output_path: str, settings: ConversionSettings,
                   job_id: str = "", hw_encoder: Optional[str] = None) -> List[List[str]]:
    """构建完整命令列表：单遍返回 [cmd]；两遍返回 [pass1, pass2]。

    注意：-map_metadata/-threads/-vf 等是输出选项，必须放在 -i 之后。
    """
    is_extract = settings.audio_mode == "extract"
    # 实际输出容器：settings.container 为空时从输出扩展名推导（跟随源容器）
    container = settings.container or os.path.splitext(output_path)[1].lstrip(".").lower()

    cmd = ["-hide_banner", "-nostdin", "-y", "-v", "error"]
    cmd += ["-i", input_path]
    if not is_extract:
        vf = build_vf(settings)
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-threads", "0", "-map_metadata", "0"]
        cmd += _video_args(settings, hw_encoder)
    cmd += _audio_args(settings)
    if (not is_extract and settings.video_codec != "copy"
            and container in ("mp4", "mov")):
        cmd += ["-movflags", "+faststart"]
    cmd += ["-progress", "pipe:1", "-nostats"]

    if settings.two_pass:
        logfile = os.path.join(tempfile.gettempdir(), "vconv_{}".format(job_id))
        pass1 = cmd + ["-pass", "1", "-passlogfile", logfile, "-an", "-f", "null", devnull_path()]
        pass2 = cmd + ["-pass", "2", "-passlogfile", logfile, output_path]
        return [pass1, pass2]

    cmd.append(output_path)
    return [cmd]
