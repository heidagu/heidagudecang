import pytest

from vconv import ffprobe_util


def test_parse_rate():
    assert ffprobe_util._parse_rate("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert ffprobe_util._parse_rate("25") == 25.0
    assert ffprobe_util._parse_rate("0/0") == 0.0
    assert ffprobe_util._parse_rate("") == 0.0
    assert ffprobe_util._parse_rate("garbage") == 0.0


def test_parse_probe_json():
    text = """
{
  "format": {"duration": "2.000000"},
  "streams": [
    {"codec_type": "video", "codec_name": "h264",
     "width": 640, "height": 360, "r_frame_rate": "30000/1001"},
    {"codec_type": "audio", "codec_name": "aac"}
  ]
}
"""
    info = ffprobe_util.parse_probe_json(text)
    assert info["duration_ms"] == 2000
    assert info["width"] == 640 and info["height"] == 360
    assert info["fps"] == pytest.approx(29.97, abs=0.01)
    assert info["vcodec"] == "h264"
    assert info["acodec"] == "aac"
    assert info["has_video"] and info["has_audio"]


def test_parse_probe_json_empty():
    info = ffprobe_util.parse_probe_json("")
    assert info == ffprobe_util.EMPTY_INFO


def test_parse_probe_json_no_audio():
    text = '{"streams": [{"codec_type": "video", "codec_name": "vp9", "width": 1920, "height": 1080}]}'
    info = ffprobe_util.parse_probe_json(text)
    assert info["has_video"] and not info["has_audio"]
    assert info["acodec"] == ""
