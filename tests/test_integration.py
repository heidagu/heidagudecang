"""集成测试：需要真实 ffmpeg（本机一键下载后运行，或 CI 安装后运行）。"""
import os
import subprocess
import time

import pytest

from vconv import command_builder, ffmpeg_util, ffprobe_util
from vconv.engine import Engine
from vconv.models import ConversionSettings

FFMPEG = ffmpeg_util.find_binary("ffmpeg")[0]
FFPROBE = ffmpeg_util.find_binary("ffprobe")[0]

pytestmark = pytest.mark.skipif(
    not FFMPEG or not FFPROBE, reason="未检测到 ffmpeg/ffprobe")


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    src = str(d / "src.mp4")
    subprocess.run(
        [FFMPEG, "-hide_banner", "-y", "-f", "lavfi",
         "-i", "testsrc=duration=2:size=320x240:rate=30",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", src],
        check=True, capture_output=True,
    )
    return src


def wait_terminal(engine, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with engine._lock:
            job = engine._jobs[job_id]
            if job.is_terminal():
                return job
        time.sleep(0.1)
    raise TimeoutError("任务未在 {}s 内结束".format(timeout))


def test_convert_h264_scale_fps(tmp_path, sample_video):
    out = str(tmp_path / "out.mp4")
    s = ConversionSettings(video_codec="h264", resolution="custom",
                           custom_width=640, custom_height=360,
                           frame_rate="25", crf=28)
    for cmd in command_builder.build_commands(sample_video, out, s):
        subprocess.run([FFMPEG] + cmd, check=True, capture_output=True)
    info = ffprobe_util.probe(FFPROBE, out)
    assert info["width"] == 640 and info["height"] == 360
    assert abs(info["fps"] - 25.0) < 0.5
    assert abs(info["duration_ms"] - 2000) < 150
    assert info["vcodec"] == "h264"


def test_engine_convert_and_history(tmp_path, sample_video, monkeypatch):
    out_dir = str(tmp_path / "engine_out")
    monkeypatch.setattr("vconv.engine.config.load_config",
                        lambda: {"default_output_dir": out_dir, "workers": 0, "ffmpeg_path": ""})
    engine = Engine()
    jobs = engine.submit([sample_video], ConversionSettings(video_codec="h264"))
    job = wait_terminal(engine, jobs[0].id)
    assert job.status == "done"
    assert os.path.isfile(job.output_path)
    assert engine._history and engine._history[-1]["id"] == job.id


def test_engine_cancel(tmp_path_factory, sample_video, monkeypatch):
    # 30 秒素材 + 慢编码器，保证取消发生时仍在转码
    d = tmp_path_factory.mktemp("cancel")
    long_src = str(d / "long.mp4")
    subprocess.run(
        [FFMPEG, "-hide_banner", "-y", "-f", "lavfi",
         "-i", "testsrc=duration=30:size=640x360:rate=30",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", long_src],
        check=True, capture_output=True,
    )
    out_dir = str(d / "out")
    monkeypatch.setattr("vconv.engine.config.load_config",
                        lambda: {"default_output_dir": out_dir, "workers": 0, "ffmpeg_path": ""})
    engine = Engine()
    # x265 默认 preset 对 640x360 也足够慢，留出取消窗口
    jobs = engine.submit([long_src], ConversionSettings(video_codec="h265"))
    job_id = jobs[0].id
    time.sleep(0.8)
    assert engine.cancel(job_id) is not None
    job = wait_terminal(engine, job_id)
    assert job.status == "cancelled"
    assert not os.path.exists(job.output_path)
