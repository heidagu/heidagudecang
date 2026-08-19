"""磁盘镜像工具：文件夹打包 ISO、提取 ISO/IMG 等内容。

- ISO 打包 / 提取：pycdlib（纯 Python，全平台，Joliet + Rock Ridge 保留长文件名与 Unicode）
- IMG/CDR/BIN 等提取：macOS hdiutil（RawDiskImage 类挂载）；Windows 检测到 7-Zip 时用 7z 解包
- 单线程任务队列：挂载 / 复制类操作一次只跑一个，互不干扰
"""
from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import pycdlib
    HAS_PYCDLIB = True
except ImportError:  # pragma: no cover
    HAS_PYCDLIB = False

logger = logging.getLogger(__name__)

SYSTEM = platform.system()
IS_MAC = SYSTEM == "Darwin"
IS_WIN = SYSTEM == "Windows"

# ---- 任务状态 ----

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_LABELS = {
    STATUS_QUEUED: "排队中", STATUS_RUNNING: "处理中", STATUS_DONE: "完成",
    STATUS_FAILED: "失败", STATUS_CANCELLED: "已取消",
}
TERMINAL = {STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED}

PACK_FORMATS = ["iso"]
EXTRACT_EXTS = {"iso", "img", "cdr", "bin", "toast", "sparseimage", "smi"}
# 挂载镜像时跳过这些 macOS 元数据目录/文件
IGNORED_NAMES = {".DS_Store", ".Trashes", ".fseventsd", ".Spotlight-V100",
                 ".TemporaryItems", ".DocumentRevisions-V100", ".apdisk"}

ISO_NAME_LIMIT = 25      # ISO9660 主名截断长度（留出 ~N;1 后缀余量，真实名靠 RR/Joliet 保留）
JOLIET_NAME_LIMIT = 64   # Joliet 组件长度上限（UCS-2 字符）


class DiscError(Exception):
    pass


class _Cancelled(Exception):
    pass


# ---- 纯函数（可单测） ----

def dedupe_file(path: str) -> str:
    """存在同名文件时追加 (1)(2)...，绝不覆盖已有文件。"""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("{0} ({1}){2}".format(root, i, ext)):
        i += 1
    return "{0} ({1}){2}".format(root, i, ext)


def dedupe_dir(path: str) -> str:
    if not os.path.exists(path):
        return path
    i = 1
    while os.path.exists("{} ({})".format(path, i)):
        i += 1
    return "{} ({})".format(path, i)


def attach_cmd(img: str, mountpoint: str) -> List[str]:
    return ["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mountpoint, img]


def attach_raw_cmd(img: str, mountpoint: str) -> List[str]:
    """IMG/ISO 原始镜像挂载兜底：强制按 RawDiskImage 类解析。"""
    return attach_cmd(img, mountpoint) + ["-imagekey", "diskimage-class=CRawDiskImage"]


def detach_cmd(mountpoint: str) -> List[str]:
    return ["hdiutil", "detach", mountpoint]


def find_7z() -> str:
    """查找 7-Zip 可执行文件（PATH 或 Windows 常见安装位置）。"""
    for name in ("7z", "7za", "7zz"):
        p = shutil.which(name)
        if p:
            return p
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        cand = os.path.join(base, "7-Zip", "7z.exe")
        if cand and os.path.isfile(cand):
            return cand
    return ""


def _isoify(name: str, used: set) -> str:
    """把文件名映射为合法且唯一的 ISO9660 主名（大写、ASCII、截断、冲突加 ~N）。"""
    clean = "".join(
        ch if ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_." else "_"
        for ch in name.upper()
    )
    clean = clean[:ISO_NAME_LIMIT] or "_"
    base = clean
    i = 1
    while clean in used:
        clean = base[:ISO_NAME_LIMIT - 2] + "~" + str(i)
        i += 1
    used.add(clean)
    return clean


# ---- ISO 打包 / 提取（pycdlib） ----

def _add_tree(iso, src: str, iso_base: str, joliet_base: str,
              on_file=None) -> None:
    used = set()
    for name in sorted(os.listdir(src)):
        if name in IGNORED_NAMES:
            continue
        full = os.path.join(src, name)
        iso_name = _isoify(name, used)
        iso_path = iso_base + iso_name
        joliet_path = ("/" if joliet_base == "/" else joliet_base + "/") + name[:JOLIET_NAME_LIMIT]
        if os.path.isdir(full) and not os.path.islink(full):
            iso.add_directory(iso_path, rr_name=name, joliet_path=joliet_path)
            _add_tree(iso, full, iso_path + "/", joliet_path, on_file)
        else:
            iso.add_file(full, iso_path=iso_path + ";1", rr_name=name, joliet_path=joliet_path)
            if on_file:
                on_file()


def create_iso(src_dir: str, out_iso: str, progress_cb=None) -> None:
    """把目录打包成 ISO（Joliet 3 + Rock Ridge 1.09）。"""
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09")
    try:
        total = sum(len(files) for _, _, files in os.walk(src_dir))
        done = [0]

        def on_file():
            done[0] += 1
            if progress_cb:
                progress_cb(done[0], max(total, 1))

        _add_tree(iso, src_dir, "/", "/", on_file)
        iso.write(out_iso)
    finally:
        iso.close()


def _strip_version(name: str) -> str:
    """去掉 ISO9660 版本后缀（;1），仅当形如 ;数字 结尾时。"""
    m = re.search(r";\d+$", name)
    return name[:m.start()] if m else name


def extract_iso(img: str, dest: str, progress_cb=None, cancel_check=None) -> None:
    """把 ISO 内容解到 dest（自动创建目录）。

    文件名优先级：Rock Ridge 原始名 > Joliet 名 > ISO9660 名（大写 8.3，去 ;1 版本）。
    """
    iso = pycdlib.PyCdlib()
    iso.open(img)
    try:
        # 第一遍：递归收集 (kind, 镜像内路径, 目标相对路径, 是否目录)
        entries = []

        def collect(iso_dir: str, dest_rel: str) -> None:
            for rec in iso.list_children(iso_path=iso_dir):
                ident = rec.file_identifier()
                if isinstance(ident, bytes):
                    ident = ident.decode("latin-1")
                if ident in (".", ".."):
                    continue
                iso_full = iso.full_path_from_dirrecord(rec, rockridge=False)
                try:
                    rr_full = iso.full_path_from_dirrecord(rec, rockridge=True)
                    name = rr_full.rstrip("/").rsplit("/", 1)[-1]
                except Exception:
                    name = _strip_version(ident)
                rel = os.path.join(dest_rel, name) if dest_rel else name
                entries.append(("iso", iso_full, rel, rec.is_dir()))
                if rec.is_dir():
                    collect(iso_full, rel)

        if iso.has_rock_ridge():
            collect("/", "")
        elif iso.has_joliet():
            facade = iso.get_joliet_facade()
            for dirpath, dirlist, filelist in facade.walk("/"):
                for d in dirlist:
                    full = dirpath.rstrip("/") + "/" + d
                    rel = (dirpath.lstrip("/") + "/" + d).lstrip("/")
                    entries.append(("joliet", full, rel, True))
                for f in filelist:
                    full = dirpath.rstrip("/") + "/" + f
                    rel = (dirpath.lstrip("/") + "/" + f).lstrip("/")
                    entries.append(("joliet", full, rel, False))
        else:
            for dirpath, dirlist, filelist in iso.walk(iso_path="/"):
                for d in dirlist:
                    full = dirpath.rstrip("/") + "/" + d
                    rel = (dirpath.lstrip("/") + "/" + d).lstrip("/")
                    entries.append(("iso", full, rel, True))
                for f in filelist:
                    full = dirpath.rstrip("/") + "/" + f
                    rel = (dirpath.lstrip("/") + "/" + _strip_version(f)).lstrip("/")
                    entries.append(("iso", full, rel, False))

        # 第二遍：复制
        total = sum(1 for _, _, _, is_dir in entries if not is_dir) or 1
        done = [0]
        for kind, src_key, rel, is_dir in entries:
            if cancel_check and cancel_check():
                raise _Cancelled()
            dest_path = os.path.join(dest, rel)
            if is_dir:
                os.makedirs(dest_path, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            try:
                with open(dest_path, "wb") as out:
                    if kind == "joliet":
                        facade.get_file_from_iso_fp(out, src_key)
                    else:
                        iso.get_file_from_iso_fp(out, iso_path=src_key)
            except Exception as e:      # 符号链接等特殊记录无法按文件复制，跳过
                logger.warning("提取 %s 失败，跳过: %s", src_key, e)
            done[0] += 1
            if progress_cb:
                progress_cb(done[0], total)
    finally:
        iso.close()


# ---- 任务 ----

@dataclass
class DiscTask:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    kind: str = ""                      # pack / extract
    status: str = STATUS_QUEUED
    progress: float = 0.0               # 0-100；打包 DMG 等无进度时为 -1（前端显示不确定条）
    message: str = ""
    error: str = ""
    source: str = ""
    dest: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    cancel_requested: bool = False

    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "status": self.status,
            "progress": round(self.progress, 1), "message": self.message,
            "error": self.error, "source": self.source, "dest": self.dest,
            "created_at": self.created_at, "finished_at": self.finished_at,
        }


class DiscManager:
    """单例镜像任务管理器：一个 worker 线程顺序执行队列中的任务。"""

    def __init__(self) -> None:
        self._tasks: Dict[str, DiscTask] = {}
        self._lock = threading.RLock()
        self._queue: deque = deque()
        self._thread: Optional[threading.Thread] = None

    # ---- 提交 / 查询 / 取消 ----

    def submit_pack(self, src_dir: str, fmt: str, out_dir: str) -> DiscTask:
        src = os.path.abspath(src_dir)
        if not os.path.isdir(src):
            raise DiscError("源文件夹不存在: {}".format(src_dir))
        if fmt not in PACK_FORMATS:
            raise DiscError("不支持的镜像格式: {}（支持 ISO）".format(fmt))
        if not HAS_PYCDLIB:
            raise DiscError("缺少 pycdlib 依赖，无法打包 ISO")
        out_dir = os.path.abspath(out_dir or os.path.dirname(src))
        if not os.path.isdir(out_dir):
            raise DiscError("输出目录不存在: {}".format(out_dir))
        dest = dedupe_file(os.path.join(out_dir, os.path.basename(src.rstrip("/\\")) + "." + fmt))
        return self._enqueue(DiscTask(kind="pack", source=src, dest=dest))

    def submit_extract(self, img_path: str, dest_dir: str) -> DiscTask:
        img = os.path.abspath(img_path)
        if not os.path.isfile(img):
            raise DiscError("镜像文件不存在: {}".format(img_path))
        ext = os.path.splitext(img)[1].lstrip(".").lower()
        if ext not in EXTRACT_EXTS:
            raise DiscError("不支持的镜像格式: .{}（支持 iso/dmg/img/cdr 等）".format(ext))
        if ext == "iso" and not HAS_PYCDLIB:
            raise DiscError("缺少 pycdlib 依赖，无法提取 ISO")
        if ext != "iso" and not IS_MAC:
            if not find_7z():
                raise DiscError("Windows 提取 {} 需要安装 7-Zip".format(ext.upper()))
        if not dest_dir:
            dest_dir = os.path.join(os.path.dirname(img),
                                    os.path.splitext(os.path.basename(img))[0] + "-提取")
        dest = dedupe_dir(os.path.abspath(dest_dir))
        return self._enqueue(DiscTask(kind="extract", source=img, dest=dest))

    def _enqueue(self, task: DiscTask) -> DiscTask:
        with self._lock:
            self._tasks[task.id] = task
            self._queue.append(task.id)
            self._ensure_worker_locked()
        return task

    def cancel(self, task_id: str) -> Optional[DiscTask]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.is_terminal():
                return None
            task.cancel_requested = True
            return task

    def delete(self, task_id: str) -> Optional[str]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return "任务不存在"
            if not task.is_terminal():
                return "任务尚未结束，无法删除"
            del self._tasks[task_id]
            return None

    def list_tasks(self) -> List[dict]:
        with self._lock:
            return [t.to_dict() for t in self._tasks.values()]

    # ---- worker ----

    def _ensure_worker_locked(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, name="vconv-disc", daemon=True)
            self._thread.start()

    def _worker(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._thread = None
                    return
                task = self._tasks[self._queue.popleft()]
            with self._lock:
                task.status = STATUS_RUNNING
                task.message = "正在打包…" if task.kind == "pack" else "正在提取…"
            try:
                if task.kind == "pack":
                    self._run_pack(task)
                else:
                    self._run_extract(task)
            except _Cancelled:
                self._finish(task, STATUS_CANCELLED)
            except DiscError as e:
                self._finish(task, STATUS_FAILED, error=str(e))
            except Exception as e:
                logger.exception("镜像任务 %s 异常", task.id)
                self._finish(task, STATUS_FAILED, error=str(e))

    # ---- 打包 ----

    def _run_pack(self, task: DiscTask) -> None:
        def cb(done, total):
            if task.cancel_requested:
                raise _Cancelled()
            with self._lock:
                task.progress = min(99, done * 100.0 / total)
                task.message = "正在写入 {}/{}".format(done, total)
        create_iso(task.source, task.dest, progress_cb=cb)
        with self._lock:
            task.message = ""
        self._finish(task, STATUS_DONE)

    # ---- 提取 ----

    def _run_extract(self, task: DiscTask) -> None:
        ext = os.path.splitext(task.source)[1].lstrip(".").lower()
        if IS_MAC and ext != "iso":
            self._extract_mounted(task)
        elif ext == "iso":
            def cb(done, total):
                if task.cancel_requested:
                    raise _Cancelled()
                with self._lock:
                    task.progress = min(99, done * 100.0 / total)
                    task.message = "正在提取 {}/{}".format(done, total)
            extract_iso(task.source, task.dest, progress_cb=cb,
                        cancel_check=lambda: task.cancel_requested)
            with self._lock:
                task.message = ""
            self._finish(task, STATUS_DONE)
        else:
            self._extract_7z(task)

    def _extract_mounted(self, task: DiscTask) -> None:
        """macOS：挂载（ISO/DMG/IMG 通用，失败回退 RawDiskImage）→ 复制 → 卸载。"""
        mountpoint = os.path.join(tempfile.gettempdir(), "vconv_mnt_" + task.id)
        rc, err = self._run(attach_cmd(task.source, mountpoint), task)
        if rc != 0 and not task.cancel_requested:
            rc, err = self._run(attach_raw_cmd(task.source, mountpoint), task)
        if task.cancel_requested:
            raise _Cancelled()
        if rc != 0:
            raise DiscError("挂载镜像失败: {}".format(err or "退出码 {}".format(rc)))
        try:
            self._copy_tree(mountpoint, task.dest, task)
        finally:
            self._run(detach_cmd(mountpoint), task, cancelable=False)
            try:
                if os.path.isdir(mountpoint):
                    shutil.rmtree(mountpoint, ignore_errors=True)
            except OSError:
                pass
        with self._lock:
            task.message = ""
        self._finish(task, STATUS_DONE)

    def _copy_tree(self, src: str, dest: str, task: DiscTask) -> None:
        total = sum(len(files) for _, _, files in os.walk(src)) or 1
        done = [0]

        def copy_one(s, d):
            if task.cancel_requested:
                raise _Cancelled()
            shutil.copy2(s, d)
            done[0] += 1
            with self._lock:
                task.progress = min(99, done[0] * 100.0 / total)
                task.message = "正在复制 {}/{}".format(done[0], total)

        shutil.copytree(src, dest, copy_function=copy_one,
                        ignore=shutil.ignore_patterns(*IGNORED_NAMES), dirs_exist_ok=True)

    def _extract_7z(self, task: DiscTask) -> None:
        sevenz = find_7z()
        if not sevenz:
            raise DiscError("未找到 7-Zip，无法提取该格式")

        def on_stdout(line):
            m = re.search(r"(\d+)\s*%", line)
            if m and not task.cancel_requested:
                with self._lock:
                    task.progress = min(99, int(m.group(1)))
                    task.message = "正在提取… {}%".format(int(m.group(1)))

        rc, err = self._run([sevenz, "x", "-y", "-o" + task.dest, task.source],
                            task, on_stdout=on_stdout)
        if task.cancel_requested:
            raise _Cancelled()
        if rc != 0:
            raise DiscError("提取失败: {}".format(err or "退出码 {}".format(rc)))
        with self._lock:
            task.message = ""
        self._finish(task, STATUS_DONE)

    # ---- 子进程 ----

    def _run(self, cmd: List[str], task: DiscTask, on_stdout=None,
             cancelable: bool = True):
        """运行子命令；返回 (rc, stderr 尾部)。取消时 terminate。"""
        err_tail = deque(maxlen=100)
        stdout = subprocess.PIPE if on_stdout else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(cmd, stdout=stdout, stderr=subprocess.PIPE)
        except OSError as e:
            raise DiscError("启动失败 {}: {}".format(" ".join(cmd[:2]), e))

        def drain_err():
            for raw in proc.stderr:
                err_tail.append(raw.decode("utf-8", errors="replace"))

        def drain_out():
            for raw in proc.stdout:
                on_stdout(raw.decode("utf-8", errors="replace"))

        threads = [threading.Thread(target=drain_err, daemon=True)]
        if on_stdout:
            threads.append(threading.Thread(target=drain_out, daemon=True))
        for t in threads:
            t.start()
        while True:
            try:
                rc = proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancelable and task.cancel_requested:
                    proc.terminate()
        for t in threads:
            t.join(timeout=5)
        return rc, "".join(err_tail).strip()[-4000:]

    # ---- 收尾 ----

    def _finish(self, task: DiscTask, status: str, error: str = "") -> None:
        with self._lock:
            if task.is_terminal():
                return
            task.status = status
            task.error = error
            task.finished_at = time.time()
            if status == STATUS_DONE:
                task.progress = 100.0
        if status in (STATUS_FAILED, STATUS_CANCELLED):
            # 清理半成品：输出文件或已解出的部分目录
            try:
                if os.path.isfile(task.dest):
                    os.remove(task.dest)
                elif os.path.isdir(task.dest):
                    shutil.rmtree(task.dest, ignore_errors=True)
            except OSError as e:
                logger.warning("清理半成品失败 %s: %s", task.dest, e)


_manager = DiscManager()


def capabilities() -> dict:
    """平台能力：{platform, create: [...], extract: [...]}。"""
    create = ["iso"] if HAS_PYCDLIB else []
    extract = ["iso"] if HAS_PYCDLIB else []
    if IS_MAC:
        extract += ["img", "cdr", "bin"]
    elif IS_WIN and find_7z():
        extract += ["img", "bin"]
    return {
        "platform": "mac" if IS_MAC else ("windows" if IS_WIN else "other"),
        "create": create,
        "extract": extract,
    }


def submit_pack(src_dir: str, fmt: str, out_dir: str = "") -> DiscTask:
    return _manager.submit_pack(src_dir, fmt, out_dir)


def submit_extract(img_path: str, dest_dir: str = "") -> DiscTask:
    return _manager.submit_extract(img_path, dest_dir)


def cancel_disc(task_id: str) -> Optional[DiscTask]:
    return _manager.cancel(task_id)


def delete_disc(task_id: str) -> Optional[str]:
    return _manager.delete(task_id)


def list_disc_tasks() -> List[dict]:
    return _manager.list_tasks()
