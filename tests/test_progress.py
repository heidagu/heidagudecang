from vconv.progress import ProgressParser, _parse_time


def test_parse_time():
    assert _parse_time("00:01:30.500000") == 90500
    assert _parse_time("bad") == 0
    assert _parse_time("") == 0


def test_out_time_us():
    p = ProgressParser()
    p.feed_line("out_time_us=1500000")
    assert p.out_time_ms == 1500


def test_out_time_ms_is_microseconds():
    p = ProgressParser()
    p.feed_line("out_time_ms=1500000")
    assert p.out_time_ms == 1500


def test_speed_and_percent():
    p = ProgressParser()
    p.feed_line("out_time_us=5000000")
    p.feed_line("speed=2.5x")
    assert p.percent(20000) == 25.0
    assert p.eta_seconds(20000) == 6


def test_percent_clamp_and_unknown_duration():
    p = ProgressParser()
    p.feed_line("out_time_us=100000")
    assert p.percent(1000) == 10.0
    p.feed_line("out_time_us=2000000")
    assert p.percent(1000) == 100.0          # clamp 上限
    assert p.percent(0) == 0.0
    assert p.eta_seconds(0) is None
    p2 = ProgressParser()
    assert p2.eta_seconds(20000) is None      # 无速度
    p3 = ProgressParser()
    p3.feed_line("speed=bad")
    assert p3.eta_seconds(20000) is None      # 速度格式错误


def test_progress_end_flag():
    p = ProgressParser()
    assert not p.finished
    p.feed_line("progress=end")
    assert p.finished
