from vconv import command_builder as cb
from vconv.models import ConversionSettings


def S(**kw):
    s = ConversionSettings()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_h264_default():
    cmd = cb.build_commands("in.mp4", "out.mp4", S())[0]
    assert cmd[cmd.index("-i") + 1] == "in.mp4"
    assert cmd[-1] == "out.mp4"
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-crf") + 1] == "23"
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert "-threads" in cmd and "0" in cmd
    assert "-movflags" in cmd and "+faststart" in cmd
    assert "-map_metadata" in cmd


def test_no_vf_when_source():
    assert "-vf" not in cb.build_commands("a.mp4", "b.mp4", S())[0]


def test_scale_and_fps_combined():
    cmd = cb.build_commands("a.mp4", "b.mp4", S(resolution="1080p", frame_rate="30"))[0]
    assert cmd[cmd.index("-vf") + 1] == "scale=-2:1080,fps=30"


def test_fps_23976_rational():
    cmd = cb.build_commands("a.mp4", "b.mp4", S(frame_rate="23.976"))[0]
    assert "fps=24000/1001" in cmd


def test_vp9_crf_requires_bv0():
    cmd = cb.build_commands("a.mp4", "b.webm", S(video_codec="vp9", container="webm", crf=31))[0]
    assert cmd[cmd.index("-c:v") + 1] == "libvpx-vp9"
    assert cmd[cmd.index("-crf") + 1] == "31"
    assert cmd[cmd.index("-b:v") + 1] == "0"
    assert "-pix_fmt" not in cmd


def test_h265_encoder_and_crf():
    cmd = cb.build_commands("a.mp4", "b.mp4", S(video_codec="h265", crf=28))[0]
    assert cmd[cmd.index("-c:v") + 1] == "libx265"
    assert cmd[cmd.index("-crf") + 1] == "28"


def test_copy_no_pixfmt_no_faststart():
    cmd = cb.build_commands("a.mp4", "b.mp4", S(video_codec="copy"))[0]
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-pix_fmt" not in cmd
    assert "-movflags" not in cmd
    assert "-crf" not in cmd


def test_bitrate_mode():
    cmd = cb.build_commands("a.mp4", "b.mp4", S(quality_mode="bitrate", bitrate="800k"))[0]
    assert cmd[cmd.index("-b:v") + 1] == "800k"
    assert "-crf" not in cmd


def test_two_pass():
    cmds = cb.build_commands("a.mp4", "b.mp4",
                             S(two_pass=True, quality_mode="bitrate", bitrate="6M"), job_id="abc")
    assert len(cmds) == 2
    p1, p2 = cmds
    assert p1[p1.index("-pass") + 1] == "1"
    assert p2[p2.index("-pass") + 1] == "2"
    assert "-an" in p1 and "-f" in p1
    assert p1[-1] in ("/dev/null", "NUL")
    assert p2[-1] == "b.mp4"
    assert p1[p1.index("-passlogfile") + 1] == p2[p2.index("-passlogfile") + 1]


def test_nvenc_quality():
    cmd = cb.build_commands("a.mp4", "b.mp4",
                            S(hw_accel=True, hw_quality="high"), hw_encoder="h264_nvenc")[0]
    assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
    assert cmd[cmd.index("-cq") + 1] == "18"
    assert "-crf" not in cmd
    assert "-pix_fmt" not in cmd


def test_videotoolbox_quality():
    cmd = cb.build_commands("a.mp4", "b.mp4",
                            S(video_codec="h265", hw_accel=True, hw_quality="low"),
                            hw_encoder="hevc_videotoolbox")[0]
    assert cmd[cmd.index("-c:v") + 1] == "hevc_videotoolbox"
    assert cmd[cmd.index("-q:v") + 1] == "80"


def test_amf_uses_bitrate_fallback():
    cmd = cb.build_commands("a.mp4", "b.mp4",
                            S(hw_accel=True, hw_quality="low"), hw_encoder="h264_amf")[0]
    assert cmd[cmd.index("-b:v") + 1] == "3M"


def test_audio_modes():
    cmd = cb.build_commands("a.mp4", "b.mp4", S(audio_mode="aac"))[0]
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "192k"

    assert "-an" in cb.build_commands("a.mp4", "b.mp4", S(audio_mode="none"))[0]

    cmd3 = cb.build_commands("a.mp4", "b.m4a", S(audio_mode="extract", audio_extract_ext="m4a"))[0]
    assert "-vn" in cmd3
    assert cmd3[cmd3.index("-c:a") + 1] == "aac"
    assert "-vf" not in cmd3


def test_copy_video_transcode_audio():
    cmd = cb.build_commands("a.mp4", "b.mp4", S(video_codec="copy", audio_mode="aac"))[0]
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"


def test_faststart_only_mp4_mov():
    assert "-movflags" in cb.build_commands("a.mp4", "b.mp4", S(container="mp4"))[0]
    assert "-movflags" in cb.build_commands("a.mp4", "b.mov", S(container="mov"))[0]
    assert "-movflags" not in cb.build_commands("a.mp4", "b.mkv", S(container="mkv"))[0]


def test_vp8_crf_requires_bv0():
    cmd = cb.build_commands("a.mp4", "b.webm", S(video_codec="vp8", container="webm", crf=10))[0]
    assert cmd[cmd.index("-c:v") + 1] == "libvpx"
    assert cmd[cmd.index("-crf") + 1] == "10"
    assert cmd[cmd.index("-b:v") + 1] == "0"


def test_qscale_mode():
    for codec in ("mpeg4", "mpeg2video", "mjpeg"):
        cmd = cb.build_commands("a.mp4", "b.avi", S(video_codec=codec, container="avi",
                                                   quality_mode="qscale", qscale=5))[0]
        assert cmd[cmd.index("-c:v") + 1] == codec
        assert cmd[cmd.index("-q:v") + 1] == "5"
        assert "-crf" not in cmd


def test_prores_profile_no_quality_args():
    cmd = cb.build_commands("a.mp4", "b.mov", S(video_codec="prores", container="mov",
                                                prores_profile="hq"))[0]
    assert cmd[cmd.index("-c:v") + 1] == "prores_ks"
    assert cmd[cmd.index("-profile:v") + 1] == "3"
    assert "-crf" not in cmd and "-b:v" not in cmd and "-q:v" not in cmd


def test_flac_audio_no_bitrate():
    cmd = cb.build_commands("a.mp4", "b.mkv", S(audio_mode="flac", container="mkv"))[0]
    assert cmd[cmd.index("-c:a") + 1] == "flac"
    assert "-b:a" not in cmd


def test_ac3_audio():
    cmd = cb.build_commands("a.mp4", "b.mkv", S(audio_mode="ac3", container="mkv"))[0]
    assert cmd[cmd.index("-c:a") + 1] == "ac3"
    assert cmd[cmd.index("-b:a") + 1] == "192k"


def test_extract_lossless_no_bitrate():
    cmd = cb.build_commands("a.mp4", "b.wav", S(audio_mode="extract", audio_extract_ext="wav"))[0]
    assert "-vn" in cmd
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le"
    assert "-b:a" not in cmd

    cmd2 = cb.build_commands("a.mp4", "b.flac", S(audio_mode="extract", audio_extract_ext="flac"))[0]
    assert cmd2[cmd2.index("-c:a") + 1] == "flac"
    assert "-b:a" not in cmd2

    cmd3 = cb.build_commands("a.mp4", "b.ac3", S(audio_mode="extract", audio_extract_ext="ac3"))[0]
    assert cmd3[cmd3.index("-c:a") + 1] == "ac3"
    assert cmd3[cmd3.index("-b:a") + 1] == "192k"


def test_m4v_faststart():
    cmd = cb.build_commands("a.mp4", "b.m4v", S(container="m4v"))[0]
    assert "-movflags" in cmd and "+faststart" in cmd
