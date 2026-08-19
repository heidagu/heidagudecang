"""任务与转换设置的数据模型。"""
from __future__ import annotations

import dataclasses
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

# ---- 视频编码 ----
# 常用编码（UI 中与「更多格式」分组隔开）
COMMON_VIDEO_CODECS = ["h264", "h265", "av1", "vp9", "copy"]
VIDEO_CODECS = COMMON_VIDEO_CODECS + ["mpeg4", "mpeg2video", "vp8", "prores", "mjpeg"]
SOFTWARE_ENCODERS = {
    "h264": "libx264", "h265": "libx265", "av1": "libsvtav1", "vp9": "libvpx-vp9",
    "mpeg4": "mpeg4", "mpeg2video": "mpeg2video", "vp8": "libvpx",
    "prores": "prores_ks", "mjpeg": "mjpeg",
}
CRF_SPECS = {
    "h264": {"default": 23, "min": 18, "max": 28},
    "h265": {"default": 28, "min": 20, "max": 32},
    "av1": {"default": 30, "min": 20, "max": 40},
    "vp9": {"default": 31, "min": 15, "max": 35},
    "vp8": {"default": 10, "min": 4, "max": 63},
}
# 固定质量档位（-q:v，数值越小质量越高）的编码
QSCALE_SPECS = {
    "mpeg4": {"default": 5, "min": 2, "max": 31},
    "mpeg2video": {"default": 5, "min": 2, "max": 31},
    "mjpeg": {"default": 5, "min": 2, "max": 31},
}
# ProRes 质量档位 → -profile:v 值
PRORES_PROFILES = {"proxy": "0", "lt": "1", "standard": "2", "hq": "3"}

# ---- 容器 ----
# 常用容器（UI 中与「更多格式」分组隔开）
COMMON_CONTAINERS = ["mp4", "mkv", "mov", "webm"]
CONTAINERS = COMMON_CONTAINERS + ["avi", "flv", "m4v", "ts"]
# 容器与视频编码的静态兼容（copy 需运行时用源编码判断，见 engine.copy_compatible）
CONTAINER_CODECS = {
    "mp4": {"h264", "h265", "av1", "mpeg4", "copy"},
    "mkv": {"h264", "h265", "av1", "vp9", "vp8", "mpeg4", "mpeg2video", "prores", "mjpeg", "copy"},
    "mov": {"h264", "h265", "mpeg4", "prores", "mjpeg", "copy"},
    "webm": {"vp9", "vp8", "av1", "copy"},
    "avi": {"mpeg4", "mpeg2video", "mjpeg", "copy"},
    "flv": {"h264", "mpeg4", "copy"},
    "m4v": {"h264", "h265", "mpeg4", "copy"},
    "ts": {"h264", "h265", "mpeg2video", "copy"},
}
# 容器与音频模式兼容
CONTAINER_AUDIO = {
    "mp4": {"copy", "aac", "flac", "none"},
    "mkv": {"copy", "aac", "opus", "mp3", "flac", "ac3", "none"},
    "mov": {"copy", "aac", "flac", "none"},
    "webm": {"copy", "opus", "none"},
    "avi": {"copy", "mp3", "ac3", "none"},
    "flv": {"copy", "aac", "mp3", "none"},
    "m4v": {"copy", "aac", "none"},
    "ts": {"copy", "aac", "mp3", "ac3", "none"},
}

# ---- 音频 ----
# 常用音频模式（UI 中与「更多格式」分组隔开）
COMMON_AUDIO_MODES = ["copy", "aac", "opus", "mp3", "none", "extract"]
AUDIO_MODES = COMMON_AUDIO_MODES + ["flac", "ac3"]
AUDIO_EXTRACT_EXTS = {
    "m4a": "aac", "opus": "libopus", "mp3": "libmp3lame",
    "wav": "pcm_s16le", "flac": "flac", "ac3": "ac3",
}

# ---- 帧率 / 分辨率 / 码率 ----

FRAME_RATES = ["source", "23.976", "24", "25", "30", "50", "60", "custom"]
RESOLUTIONS = ["source", "4k", "1080p", "720p", "custom"]
BITRATE_RE = re.compile(r"^\d+(k|M)$")
AUDIO_BITRATE_RE = re.compile(r"^\d+k$")

# ---- 任务状态 ----

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_LABELS = {
    STATUS_QUEUED: "排队中",
    STATUS_RUNNING: "转换中",
    STATUS_DONE: "完成",
    STATUS_FAILED: "失败",
    STATUS_CANCELLED: "已取消",
}
TERMINAL_STATUSES = {STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED}


@dataclass
class ConversionSettings:
    """一次转换的参数集合。由 API 层从 JSON 构造并调用 validate()。"""

    container: str = ""                 # 空 = 跟随源文件容器
    video_codec: str = "h264"           # h264/h265/av1/vp9/copy
    frame_rate: str = "source"          # source/23.976/24/25/30/50/60/custom
    custom_fps: float = 30.0            # frame_rate=custom 时使用，1-120
    resolution: str = "source"          # source/4k/1080p/720p/custom
    custom_width: int = 0               # resolution=custom 时使用，2-8192
    custom_height: int = 0
    quality_mode: str = "crf"           # crf/bitrate/qscale
    crf: int = 23
    qscale: int = 5                     # quality_mode=qscale 时使用（越小质量越高）
    bitrate: str = "6M"                 # 如 6M / 800k
    prores_profile: str = "standard"    # prores 质量档位：proxy/lt/standard/hq
    two_pass: bool = False
    hw_accel: bool = False
    hw_quality: str = "medium"          # high/medium/low（硬件编码器画质预设）
    audio_mode: str = "copy"            # copy/aac/opus/mp3/none/extract
    audio_bitrate: str = "192k"         # 如 192k
    audio_extract_ext: str = "m4a"      # extract 模式输出扩展名：m4a/opus/mp3

    @classmethod
    def from_dict(cls, data: dict) -> "ConversionSettings":
        allowed = {f.name for f in dataclasses.fields(cls)}
        kwargs = {k: v for k, v in (data or {}).items() if k in allowed}
        s = cls(**kwargs)
        # 未显式指定 CRF 时，按编码套用各自默认值
        if "crf" not in kwargs and s.video_codec in CRF_SPECS:
            s.crf = CRF_SPECS[s.video_codec]["default"]
        if "qscale" not in kwargs and s.video_codec in QSCALE_SPECS:
            s.qscale = QSCALE_SPECS[s.video_codec]["default"]
        return s

    def validate(self) -> List[str]:
        """返回中文错误列表；空列表表示通过。"""
        errors = []
        if self.video_codec not in VIDEO_CODECS:
            errors.append("不支持的视频编码: {}".format(self.video_codec))
            return errors
        if self.container and self.container not in CONTAINERS:
            errors.append("不支持的封装容器: {}".format(self.container))
        if (self.container and self.video_codec != "copy"
                and self.video_codec not in CONTAINER_CODECS.get(self.container, set())):
            errors.append("容器 {} 不支持 {} 编码".format(self.container, self.video_codec))
        if (self.container and self.audio_mode != "extract"
                and self.audio_mode not in CONTAINER_AUDIO.get(self.container, set())):
            errors.append("容器 {} 不支持 {} 音频模式".format(self.container, self.audio_mode))

        if self.frame_rate not in FRAME_RATES:
            errors.append("不支持的帧率选项: {}".format(self.frame_rate))
        if self.frame_rate == "custom" and not (1.0 <= float(self.custom_fps) <= 120.0):
            errors.append("自定义帧率必须在 1-120 之间")

        if self.resolution not in RESOLUTIONS:
            errors.append("不支持的分辨率选项: {}".format(self.resolution))
        if self.resolution == "custom":
            for label, v in (("宽度", self.custom_width), ("高度", self.custom_height)):
                if not (2 <= int(v) <= 8192):
                    errors.append("自定义分辨率{}必须是 2-8192 的整数".format(label))

        if self.quality_mode not in ("crf", "bitrate", "qscale"):
            errors.append("不支持的画质模式: {}".format(self.quality_mode))
        if (self.quality_mode == "crf" and self.video_codec in QSCALE_SPECS):
            errors.append("{} 不支持 CRF 画质，请使用质量档位".format(self.video_codec))
        if self.quality_mode == "crf" and self.video_codec in CRF_SPECS:
            spec = CRF_SPECS[self.video_codec]
            if not (spec["min"] <= int(self.crf) <= spec["max"]):
                errors.append("{} 的 CRF 范围是 {}-{}".format(
                    self.video_codec, spec["min"], spec["max"]))
        if self.quality_mode == "qscale" and self.video_codec not in QSCALE_SPECS:
            errors.append("{} 不支持质量档位画质".format(self.video_codec))
        if self.quality_mode == "qscale" and self.video_codec in QSCALE_SPECS:
            spec = QSCALE_SPECS[self.video_codec]
            if not (spec["min"] <= int(self.qscale) <= spec["max"]):
                errors.append("{} 的质量档位范围是 {}-{}（越小质量越高）".format(
                    self.video_codec, spec["min"], spec["max"]))
        if (self.quality_mode == "bitrate" and self.video_codec == "prores"):
            errors.append("ProRes 不支持固定码率，请使用质量档位")
        if self.quality_mode == "bitrate" and not BITRATE_RE.match(self.bitrate):
            errors.append("视频码率格式无效，示例: 6M 或 800k")
        if self.video_codec == "prores" and self.prores_profile not in PRORES_PROFILES:
            errors.append("无效的 ProRes 质量档位")

        if self.two_pass:
            if self.video_codec not in ("h264", "h265", "vp9", "vp8", "mpeg4", "mpeg2video"):
                errors.append("两遍编码仅支持 h264/h265/vp9/vp8/mpeg4/mpeg2video")
            if self.quality_mode != "bitrate":
                errors.append("两遍编码需要固定码率模式")
            if self.hw_accel:
                errors.append("硬件加速不支持两遍编码")
            if self.audio_mode == "extract":
                errors.append("音频提取模式不支持两遍编码")

        if self.hw_accel and self.video_codec not in ("h264", "h265"):
            errors.append("硬件加速仅支持 h264/h265")
        if self.hw_quality not in ("high", "medium", "low"):
            errors.append("硬件加速画质预设无效")

        if self.audio_mode not in AUDIO_MODES:
            errors.append("不支持的音频模式: {}".format(self.audio_mode))
        if (self.audio_mode in ("aac", "opus", "mp3", "ac3", "extract")
                and not AUDIO_BITRATE_RE.match(self.audio_bitrate)):
            errors.append("音频码率格式无效，示例: 192k")
        if self.audio_mode == "extract" and self.audio_extract_ext not in AUDIO_EXTRACT_EXTS:
            errors.append("音频提取格式无效（m4a/opus/mp3/wav/flac/ac3）")
        return errors


@dataclass
class Job:
    """一个转换任务（一个输入文件）。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    input_path: str = ""
    output_path: str = ""
    settings: Optional[ConversionSettings] = None
    status: str = STATUS_QUEUED
    progress: float = 0.0                # 0-100（两遍编码时为总进度）
    speed: str = ""                      # 形如 "3.2x"
    out_time_ms: int = 0
    duration_ms: int = 0
    eta_seconds: Optional[int] = None
    error: str = ""
    pass_index: int = 1                  # 两遍编码当前第几遍
    pass_count: int = 1
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cancel_requested: bool = False

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "status": self.status,
            "progress": round(self.progress, 1),
            "speed": self.speed,
            "out_time_ms": self.out_time_ms,
            "duration_ms": self.duration_ms,
            "eta_seconds": self.eta_seconds,
            "error": self.error,
            "pass_index": self.pass_index,
            "pass_count": self.pass_count,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "settings": dataclasses.asdict(self.settings) if self.settings else {},
        }
