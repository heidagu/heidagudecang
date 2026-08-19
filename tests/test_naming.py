import os

from vconv import config
from vconv.engine import output_extension, resolve_output_path
from vconv.models import ConversionSettings


def _mk(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x")
    return path


def test_follows_source_container(tmp_path):
    src = _mk(str(tmp_path / "movie.mkv"))
    out = resolve_output_path(src, ConversionSettings())
    assert out == str(tmp_path / "movie (1).mkv")   # 同目录同名 → 自动改名，不覆盖源


def test_container_override(tmp_path):
    src = _mk(str(tmp_path / "a.avi"))
    out = resolve_output_path(src, ConversionSettings(container="mp4"))
    assert out.endswith("a.mp4")


def test_collision_increment(tmp_path):
    src = _mk(str(tmp_path / "a.mp4"))
    _mk(str(tmp_path / "a (1).mp4"))
    out = resolve_output_path(src, ConversionSettings())
    assert out == str(tmp_path / "a (2).mp4")


def test_extract_ext(tmp_path):
    src = _mk(str(tmp_path / "a.mp4"))
    s = ConversionSettings(audio_mode="extract", audio_extract_ext="mp3")
    out = resolve_output_path(src, s)
    assert out == str(tmp_path / "a.mp3")


def test_output_extension():
    assert output_extension(ConversionSettings(), "/x/y.mkv") == "mkv"
    assert output_extension(ConversionSettings(), "/x/y.avi") == "mp4"
    assert output_extension(ConversionSettings(container="webm"), "/x/y.mp4") == "webm"
    assert output_extension(
        ConversionSettings(audio_mode="extract", audio_extract_ext="opus"), "/x/y.mp4") == "opus"


def test_default_output_dir(tmp_path, monkeypatch):
    src = _mk(str(tmp_path / "in" / "a.mp4"))
    out_dir = tmp_path / "out"
    monkeypatch.setattr(config, "load_config",
                        lambda: {"default_output_dir": str(out_dir), "workers": 0, "ffmpeg_path": ""})
    out = resolve_output_path(src, ConversionSettings())
    assert os.path.dirname(out) == str(out_dir)
    assert os.path.isdir(out_dir)
