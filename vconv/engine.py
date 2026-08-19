"""转换任务引擎：内存任务表 + 线程池 worker + 取消 + 历史记录。"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from . import command_builder, config, ffmpeg_util, ffprobe_util
from .models import (STATUS_CANCELLED, STATUS_DONE, STATUS_FAILED, STATUS_RUNNING,
                     TERMINAL_STATUSES, ConversionSettings, Job)
from .process_utils import spawn_kwargs, terminate_tree
from .progress import ProgressParser

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 200

# copy 模式下，容器对源视频编码的兼容（mkv 基本通吃，None 表示不限制）
COPY_CODECS = {
    "mp4": {"h264", "h265", "av1", "mpeg4", "mpeg2video"},
    "mov": {"h264", "h265", "mpeg4", "prores"},
    "webm": {"vp8", "vp9", "av1"},
    "mkv": None,
}


def copy_compatible(container: str, vcodec: str) -> bool:
    allowed = COPY_CODECS.get(container)
    if allowed is None:
        return True
    return vcodec in allowed


def output_extension(settings: ConversionSettings, input_path: str) -> str:
    """输出扩展名：提取音频 > 指定容器 > 跟随源容器 > mp4。"""
    if settings.audio_mode == "extract":
        return settings.audio_extract_ext
    if settings.container:
        return settings.container
    src = os.path.splitext(input_path)[1].lstrip(".").lower()
    if src in ("mp4", "mkv", "mov", "webm"):
        return src
    return "mp4"


def _dedupe(path: str) -> str:
    """存在同名文件时追加 (1)(2)...，绝不覆盖已有文件（包括源文件）。"""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("{0} ({1}){2}".format(root, i, ext)):
        i += 1
    return "{0} ({1}){2}".format(root, i, ext)


def resolve_output_path(input_path: str, settings: ConversionSettings) -> str:
    """决定输出路径：默认输出目录（或源目录）+ 去重命名。"""
    cfg = config.load_config()
    out_dir = (cfg.get("default_output_dir") or "").strip() or os.path.dirname(input_path)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(input_path))[0]
    ext = output_extension(settings, input_path)
    return _dedupe(os.path.join(out_dir, stem + "." + ext))


class Engine:
    """单例任务引擎。worker 池按 settings.workers 惰性创建。"""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.RLock()
        self._pool: Optional[ThreadPoolExecutor] = None
        self._pool_workers = 0
        self._probe_cache: Dict[str, dict] = {}
        self._history = self._load_history()

    # ---- 历史记录 ----

    @staticmethod
    def _history_path() -> str:
        return os.path.join(config.app_data_dir(), "jobs.json")

    def _load_history(self) -> list:
        try:
            with open(self._history_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save_history(self) -> None:
        try:
            path = self._history_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._history[-HISTORY_LIMIT:], f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except OSError as e:
            logger.warning("保存历史失败: %s", e)

    def _record_history(self, job: Job) -> None:
        entry = {
            "id": job.id,
            "input": job.input_path,
            "output": job.output_path,
            "status": job.status,
            "error": job.error,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
        }
        with self._lock:
            self._history.append(entry)
            self._history = self._history[-HISTORY_LIMIT:]
        self._save_history()

    # ---- 任务池 ----

    def _ensure_pool(self) -> None:
        with self._lock:
            if self._pool is None:
                self._create_pool_locked()

    def _create_pool_locked(self) -> None:
        workers = config.resolve_workers(config.load_config())
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vconv")
        self._pool_workers = workers
        logger.info("worker 池已创建: %d 线程", workers)

    def set_workers(self, n: int) -> None:
        """调整并发数：新任务进新池，旧池在跑任务结束后自行回收。"""
        n = min(max(int(n), config.MIN_WORKERS), config.MAX_WORKERS)
        with self._lock:
            old = self._pool
            self._create_pool_locked()
        if old is not None:
            old.shutdown(wait=False)
        logger.info("worker 数调整为 %d", n)

    # ---- 提交 / 取消 / 删除 / 查询 ----

    def submit(self, inputs: List[str], settings: ConversionSettings) -> List[Job]:
        errors = settings.validate()
        if errors:
            raise ValueError("；".join(errors))
        if settings.hw_accel:
            ff_path, _ = ffmpeg_util.find_binary("ffmpeg")
            if not ff_path:
                raise ValueError("未检测到 ffmpeg，无法使用硬件加速")
            if not ffmpeg_util.detect_hw_encoders(ff_path).get(settings.video_codec):
                raise ValueError("未找到 {} 的可用硬件编码器".format(settings.video_codec))

        jobs = []
        for path in inputs:
            abs_path = os.path.abspath(path)
            if not os.path.isfile(abs_path):
                raise ValueError("文件不存在: {}".format(path))
            try:
                output = resolve_output_path(abs_path, settings)
            except OSError as e:
                raise ValueError("无法创建输出目录: {}".format(e))
            jobs.append(Job(input_path=abs_path, output_path=output, settings=settings))

        with self._lock:
            for job in jobs:
                self._jobs[job.id] = job
        self._ensure_pool()
        with self._lock:
            pool = self._pool
        for job in jobs:
            pool.submit(self._run_job, job.id)
        return jobs

    def cancel(self, job_id: str) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.is_terminal():
                return None
            job.cancel_requested = True
            return job

    def delete(self, job_id: str) -> Optional[str]:
        """删除终态任务；返回错误消息或 None。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return "任务不存在"
            if not job.is_terminal():
                return "任务尚未结束，无法删除"
            del self._jobs[job_id]
            return None

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> dict:
        with self._lock:
            jobs = [j.to_dict() for j in self._jobs.values()]
        jobs.sort(key=lambda d: d["created_at"])
        return {"jobs": jobs, "history": self._history[-HISTORY_LIMIT:]}

    # ---- worker ----

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = STATUS_RUNNING
            job.started_at = time.time()
        try:
            self._execute(job)
        except Exception as e:      # 兜底：任何未预期异常转为失败
            logger.exception("任务 %s 异常", job_id)
            self._finish(job, STATUS_FAILED, error=str(e))

    def _probe(self, ffprobe_path: str, input_path: str) -> dict:
        with self._lock:
            cached = self._probe_cache.get(input_path)
            if cached is not None:
                return cached
        try:
            info = ffprobe_util.probe(ffprobe_path, input_path)
        except Exception as e:
            logger.warning("ffprobe 失败 %s: %s", input_path, e)
            info = dict(ffprobe_util.EMPTY_INFO)
        with self._lock:
            self._probe_cache.setdefault(input_path, info)
        return info

    def _execute(self, job: Job) -> None:
        settings = job.settings
        ff_path, _ = ffmpeg_util.find_binary("ffmpeg")
        fp_path, _ = ffmpeg_util.find_binary("ffprobe")
        if not ff_path or not fp_path:
            self._finish(job, STATUS_FAILED, error="未检测到 ffmpeg/ffprobe，请先一键下载或手动安装")
            return

        info = self._probe(fp_path, job.input_path)
        job.duration_ms = info["duration_ms"]

        if (settings.video_codec == "copy" and settings.container
                and not copy_compatible(settings.container, info["vcodec"])):
            self._finish(job, STATUS_FAILED,
                         error="源视频编码 {} 无法封装进 {} 容器".format(info["vcodec"], settings.container))
            return
        if settings.audio_mode == "extract" and not info["has_audio"]:
            self._finish(job, STATUS_FAILED, error="源文件没有音轨，无法提取音频")
            return

        hw_encoder = None
        if settings.hw_accel:
            hw_encoder = ffmpeg_util.detect_hw_encoders(ff_path).get(settings.video_codec)
            if not hw_encoder:
                self._finish(job, STATUS_FAILED,
                             error="未找到 {} 的可用硬件编码器".format(settings.video_codec))
                return

        commands = command_builder.build_commands(
            job.input_path, job.output_path, settings, job_id=job.id, hw_encoder=hw_encoder)
        job.pass_count = len(commands)

        ok = True
        for index, cmd in enumerate(commands, start=1):
            with self._lock:
                if job.cancel_requested:
                    ok = False
                    break
                job.pass_index = index
                job.progress = (index - 1) * 100.0 / len(commands)
            # build_commands 是纯参数构建器，不含可执行文件；此处前置已检测到的 ffmpeg 路径
            rc, err_tail = self._run_command(job, [ff_path] + cmd)
            if job.cancel_requested:
                ok = False
                break
            if rc != 0:
                self._finish(job, STATUS_FAILED, error=err_tail)
                ok = False
                break

        self._cleanup_passlogs(job.id)
        if ok and not job.cancel_requested:
            self._finish(job, STATUS_DONE)
        elif job.cancel_requested:
            self._finish(job, STATUS_CANCELLED)

    def _run_command(self, job: Job, cmd: List[str]):
        """执行单条命令（一遍），返回 (returncode, stderr 尾部文本)。"""
        parser = ProgressParser()
        stderr_tail = deque(maxlen=200)
        try:
            proc = subprocess.Popen(cmd, **spawn_kwargs())
        except OSError as e:
            return 1, "启动 ffmpeg 失败: {}".format(e)

        # 两个读线程分别排空 stdout/stderr，防止管道缓冲写满导致死锁
        def drain_out() -> None:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace")
                parser.feed_line(line)
                with self._lock:
                    pct = parser.percent(job.duration_ms)
                    job.progress = ((job.pass_index - 1) * 100.0 / job.pass_count
                                    + pct / job.pass_count)
                    job.out_time_ms = parser.out_time_ms
                    job.speed = parser.speed
                    job.eta_seconds = parser.eta_seconds(job.duration_ms)

        def drain_err() -> None:
            for raw in proc.stderr:
                stderr_tail.append(raw.decode("utf-8", errors="replace"))

        t1 = threading.Thread(target=drain_out, daemon=True)
        t2 = threading.Thread(target=drain_err, daemon=True)
        t1.start()
        t2.start()

        while True:
            try:
                rc = proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                with self._lock:
                    if job.cancel_requested:
                        terminate_tree(proc)

        t1.join(timeout=5)
        t2.join(timeout=5)
        return rc, "".join(stderr_tail).strip()[-4000:]

    @staticmethod
    def _cleanup_passlogs(job_id: str) -> None:
        logfile = os.path.join(tempfile.gettempdir(), "vconv_{}".format(job_id))
        for suffix in ("-0.log", "-0.log.mbtree"):
            path = logfile + suffix
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _remove_partial(path: str) -> None:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as e:
            logger.warning("清理半成品失败 %s: %s", path, e)

    def _finish(self, job: Job, status: str, error: str = "") -> None:
        with self._lock:
            if job.status in TERMINAL_STATUSES:
                return      # 已终态（如取消与失败竞争时先到先得）
            job.status = status
            job.error = error
            job.finished_at = time.time()
            if status == STATUS_DONE:
                job.progress = 100.0
        if status in (STATUS_FAILED, STATUS_CANCELLED):
            self._remove_partial(job.output_path)
        self._record_history(job)
