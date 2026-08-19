from vconv.models import ConversionSettings


def S(**kw):
    s = ConversionSettings()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_default_valid():
    assert S().validate() == []


def test_webm_rejects_h264():
    assert S(container="webm", video_codec="h264").validate()


def test_mp4_rejects_opus():
    assert S(container="mp4", audio_mode="opus").validate()


def test_two_pass_requires_bitrate_sw_encoders():
    assert S(two_pass=True).validate()                      # crf 模式
    assert S(two_pass=True, quality_mode="bitrate", video_codec="av1").validate()
    assert S(two_pass=True, quality_mode="bitrate", hw_accel=True).validate()
    assert S(two_pass=True, quality_mode="bitrate", video_codec="h264").validate() == []


def test_hw_accel_only_h264_h265():
    assert S(hw_accel=True, video_codec="vp9").validate()
    assert S(hw_accel=True, video_codec="h265").validate() == []


def test_fps_and_resolution_ranges():
    assert S(frame_rate="custom", custom_fps=0.5).validate()
    assert S(frame_rate="custom", custom_fps=121).validate()
    assert S(frame_rate="custom", custom_fps=29.97).validate() == []
    assert S(resolution="custom", custom_width=1, custom_height=1080).validate()
    assert S(resolution="custom", custom_width=1920, custom_height=1080).validate() == []


def test_bitrate_formats():
    assert S(quality_mode="bitrate", bitrate="6M").validate() == []
    assert S(quality_mode="bitrate", bitrate="800k").validate() == []
    assert S(quality_mode="bitrate", bitrate="fast").validate()
    assert S(audio_mode="aac", audio_bitrate="192k").validate() == []
    assert S(audio_mode="aac", audio_bitrate="loud").validate()


def test_crf_range_per_codec():
    assert S(video_codec="h264", crf=10).validate()
    assert S(video_codec="h264", crf=23).validate() == []
    assert S(video_codec="h265", crf=32).validate() == []


def test_from_dict_applies_codec_crf_default():
    assert ConversionSettings.from_dict({"video_codec": "h265"}).crf == 28
    assert ConversionSettings.from_dict({"video_codec": "vp9"}).crf == 31
    assert ConversionSettings.from_dict({"video_codec": "av1"}).crf == 30
    # 显式指定时不覆盖
    assert ConversionSettings.from_dict({"video_codec": "vp9", "crf": 20}).crf == 20
