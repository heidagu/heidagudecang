"""ffmpeg -progress 输出解析（纯类，可单测）。"""
from __future__ import annotations


def _parse_time(value: str) -> int:
    """'HH:MM:SS.mmm' → 毫秒；解析失败返回 0。"""
    try:
        parts = value.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600000 + int(m) * 60000 + int(float(s) * 1000)
    except (ValueError, IndexError):
        pass
    return 0


class ProgressParser:
    """累计解析 ffmpeg -progress pipe:1 的 key=value 行。"""

    def __init__(self) -> None:
        self.out_time_ms = 0
        self.speed = ""       # 形如 "3.2x"
        self.fps = ""
        self.finished = False

    def feed_line(self, line: str) -> None:
        line = line.strip()
        if not line or "=" not in line:
            return
        key, _, value = line.partition("=")
        value = value.strip()
        if key == "out_time_us":
            self.out_time_ms = int(float(value)) // 1000
        elif key == "out_time_ms":
            # ffmpeg 的历史问题：out_time_ms 的值实际也是微秒
            self.out_time_ms = int(float(value)) // 1000
        elif key == "out_time":
            self.out_time_ms = _parse_time(value)
        elif key == "speed":
            self.speed = value
        elif key == "fps":
            self.fps = value
        elif key == "progress" and value == "end":
            self.finished = True

    def percent(self, duration_ms: int) -> float:
        """按输入时长换算百分比（0-100，clamp）。时长未知返回 0。"""
        if duration_ms <= 0:
            return 0.0
        p = self.out_time_ms * 100.0 / duration_ms
        return max(0.0, min(100.0, p))

    def eta_seconds(self, duration_ms: int):
        """按剩余时长与速度估算剩余秒数；无法估算返回 None。"""
        speed = self.speed
        if not speed or not speed.endswith("x") or duration_ms <= 0:
            return None
        try:
            factor = float(speed[:-1])
        except ValueError:
            return None
        if factor <= 0:
            return None
        remaining_ms = max(0, duration_ms - self.out_time_ms)
        return int(remaining_ms / 1000.0 / factor)
