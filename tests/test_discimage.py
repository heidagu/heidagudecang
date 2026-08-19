"""磁盘镜像模块测试：纯函数 + 管理器生命周期（子进程用假实现隔离）。"""
from __future__ import annotations

import os
import time

import pytest

from vconv import discimage
from vconv.discimage import (STATUS_CANCELLED, STATUS_DONE, STATUS_FAILED,
                             DiscError, DiscManager, DiscTask, _Cancelled)


def wait_terminal(m, task, timeout=5.0):
    deadline = time.time() + timeout
    while not task.is_terminal() and time.time() < deadline:
        time.sleep(0.01)
    assert task.is_terminal(), "任务未在 {}s 内结束".format(timeout)


@pytest.fixture()
def manager():
    m = DiscManager()
    yield m
    for tid in list(m._tasks):
        m._tasks.pop(tid, None)
    m._queue.clear()


# ---- 纯函数 ----

def test_dedupe():
    p = discimage.dedupe_file("/a/b.iso")
    assert p == "/a/b.iso"
    p2 = discimage.dedupe_dir("/a/b")
    assert p2 == "/a/b"


def test_dedupe_with_existing(tmp_path):
    f = tmp_path / "a.iso"
    f.write_text("x")
    assert discimage.dedupe_file(str(f)) == str(tmp_path / "a (1).iso")
    d = tmp_path / "a"
    d.mkdir()
    assert discimage.dedupe_dir(str(d)) == str(tmp_path / "a (1)")


def test_attach_cmds():
    cmd = discimage.attach_cmd("x.iso", "/mnt")
    assert cmd[0] == "hdiutil" and cmd[1] == "attach"
    assert cmd[cmd.index("-mountpoint") + 1] == "/mnt"
    assert cmd[-1] == "x.iso"
    raw = discimage.attach_raw_cmd("x.img", "/mnt")
    assert raw[raw.index("-imagekey") + 1] == "diskimage-class=CRawDiskImage"
    assert discimage.detach_cmd("/mnt") == ["hdiutil", "detach", "/mnt"]


def test_isoify_unique():
    used = set()
    assert discimage._isoify("File.txt", used) == "FILE.TXT"
    assert discimage._isoify("File.txt", used) == "FILE.TXT~1"
    assert discimage._isoify("File.txt", used) == "FILE.TXT~2"
    assert discimage._isoify("中文名.bin", used) == "___.BIN"


def test_capabilities_shape():
    cap = discimage.capabilities()
    assert cap["platform"] in ("mac", "windows", "other")
    assert isinstance(cap["create"], list) and isinstance(cap["extract"], list)
    if discimage.HAS_PYCDLIB:
        assert "iso" in cap["create"] and "iso" in cap["extract"]


# ---- 提交校验 ----

def test_submit_pack_validation(manager, tmp_path):
    with pytest.raises(DiscError, match="源文件夹不存在"):
        manager.submit_pack(str(tmp_path / "nope"), "iso", "")
    with pytest.raises(DiscError, match="不支持"):
        manager.submit_pack(str(tmp_path), "dmg", "")


def test_submit_extract_validation(manager, tmp_path):
    with pytest.raises(DiscError, match="镜像文件不存在"):
        manager.submit_extract(str(tmp_path / "nope.iso"), "")
    f = tmp_path / "x.exe"
    f.write_text("x")
    with pytest.raises(DiscError, match="不支持的镜像格式"):
        manager.submit_extract(str(f), "")


def test_extract_default_dest_dir(manager, tmp_path):
    img = tmp_path / "光盘.iso"
    img.write_text("x")
    task = manager.submit_extract(str(img), "")
    assert task.dest == str(tmp_path / "光盘-提取")
    # 已存在时自动加 (1)
    (tmp_path / "光盘-提取").mkdir()
    task2 = manager.submit_extract(str(img), "")
    assert task2.dest == str(tmp_path / "光盘-提取 (1)")


# ---- 管理器生命周期 ----

def test_pack_lifecycle(manager, monkeypatch, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hi")
    monkeypatch.setattr(manager, "_run_pack",
                        lambda task: manager._finish(task, STATUS_DONE))
    task = manager.submit_pack(str(src), "iso", "")
    assert task.status == discimage.STATUS_QUEUED
    assert task.dest.endswith("src.iso")
    wait_terminal(manager, task)
    assert task.status == STATUS_DONE and task.progress == 100.0


def test_pack_failure_cleans_partial(manager, monkeypatch, tmp_path):
    src = tmp_path / "src"
    src.mkdir()

    def fake_pack(task):
        with open(task.dest, "wb") as f:       # 伪造半成品
            f.write(b"partial")
        raise DiscError("打包失败")

    monkeypatch.setattr(manager, "_run_pack", fake_pack)
    task = manager.submit_pack(str(src), "iso", "")
    wait_terminal(manager, task)
    assert task.status == STATUS_FAILED
    assert "打包失败" in task.error
    assert not os.path.exists(task.dest)       # 半成品已清理


def test_extract_mounted_copies_and_detaches(manager, monkeypatch, tmp_path):
    img = tmp_path / "x.img"
    img.write_text("x")
    dest = tmp_path / "out"
    calls = []

    def fake_run(cmd, task, on_stdout=None, cancelable=True):
        calls.append(cmd[1])
        if cmd[1] == "attach":
            mnt = cmd[cmd.index("-mountpoint") + 1]
            os.makedirs(os.path.join(mnt, "sub"))
            with open(os.path.join(mnt, "a.txt"), "w") as f:
                f.write("hello")
            with open(os.path.join(mnt, "sub", "b.bin"), "wb") as f:
                f.write(b"\x00")
            with open(os.path.join(mnt, ".DS_Store"), "w") as f:   # 应被忽略
                f.write("junk")
        return 0, ""

    monkeypatch.setattr(manager, "_run", fake_run)
    task = DiscTask(kind="extract", source=str(img), dest=str(dest))
    manager._extract_mounted(task)
    assert task.status == STATUS_DONE
    assert (dest / "a.txt").read_text() == "hello"
    assert (dest / "sub" / "b.bin").read_bytes() == b"\x00"
    assert not (dest / ".DS_Store").exists()
    assert calls == ["attach", "detach"]       # 先挂载后卸载


def test_extract_mounted_raw_fallback(manager, monkeypatch, tmp_path):
    img = tmp_path / "x.img"
    img.write_text("x")
    dest = tmp_path / "out"
    cmds = []

    def fake_run(cmd, task, on_stdout=None, cancelable=True):
        cmds.append(cmd)
        if cmd[1] == "attach" and "diskimage-class=CRawDiskImage" not in cmd:
            return 1, "attach failed"          # 首次挂载失败 → 回退 RawDiskImage
        if cmd[1] == "attach":
            mnt = cmd[cmd.index("-mountpoint") + 1]
            os.makedirs(mnt)
            with open(os.path.join(mnt, "a.txt"), "w") as f:
                f.write("x")
        return 0, ""

    monkeypatch.setattr(manager, "_run", fake_run)
    task = DiscTask(kind="extract", source=str(img), dest=str(dest))
    manager._extract_mounted(task)
    assert task.status == STATUS_DONE
    assert any("diskimage-class=CRawDiskImage" in c for c in cmds)


def test_cancel_during_copy(manager, monkeypatch, tmp_path):
    img = tmp_path / "x.img"
    img.write_text("x")
    dest = tmp_path / "out"

    def fake_run(cmd, task, on_stdout=None, cancelable=True):
        if cmd[1] == "attach":
            mnt = cmd[cmd.index("-mountpoint") + 1]
            os.makedirs(mnt)
            with open(os.path.join(mnt, "a.txt"), "w") as f:
                f.write("x")
        return 0, ""

    monkeypatch.setattr(manager, "_run", fake_run)
    task = DiscTask(kind="extract", source=str(img), dest=str(dest))
    task.cancel_requested = True
    with pytest.raises(_Cancelled):
        manager._extract_mounted(task)


def test_worker_cancel_marks_cancelled(manager, monkeypatch, tmp_path):
    src = tmp_path / "src"
    src.mkdir()

    def fake_pack(task):
        task.cancel_requested = True
        raise _Cancelled()

    monkeypatch.setattr(manager, "_run_pack", fake_pack)
    task = manager.submit_pack(str(src), "iso", "")
    wait_terminal(manager, task)
    assert task.status == STATUS_CANCELLED


def test_cancel_and_delete(manager, monkeypatch, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(manager, "_run_pack",
                        lambda task: manager._finish(task, STATUS_DONE))
    task = manager.submit_pack(str(src), "iso", "")
    assert manager.cancel("nope") is None
    assert manager.delete(task.id) is not None       # 未结束不可删
    wait_terminal(manager, task)
    assert manager.cancel(task.id) is None           # 已终态不可取消
    assert manager.delete(task.id) is None
    assert manager.list_tasks() == []


# ---- ISO 真实回环（pycdlib 纯 Python，各平台可跑） ----

def test_iso_roundtrip_preserves_names(tmp_path):
    src = tmp_path / "src"
    (src / "子目录" / "深层").mkdir(parents=True)
    (src / "长 文件名 with spaces.txt").write_text("hello")
    (src / "子目录" / "中文名.bin").write_bytes(b"\x00\x01")
    (src / "子目录" / "深层" / "deep.log").write_text("deep")
    (src / ".DS_Store").write_text("junk")            # 打包时应忽略

    iso_path = str(tmp_path / "t.iso")
    discimage.create_iso(str(src), iso_path)
    assert os.path.isfile(iso_path)

    dest = tmp_path / "out"
    discimage.extract_iso(iso_path, str(dest))
    assert (dest / "长 文件名 with spaces.txt").read_text() == "hello"
    assert (dest / "子目录" / "中文名.bin").read_bytes() == b"\x00\x01"
    assert (dest / "子目录" / "深层" / "deep.log").read_text() == "deep"
    assert not (dest / ".DS_Store").exists()


def test_iso_joliet_only_extraction(tmp_path):
    """无 Rock Ridge 的 ISO（如 hdiutil -joliet 生成）走 Joliet 名提取。"""
    import pycdlib

    src = tmp_path / "src"
    (src / "子目录").mkdir(parents=True)
    (src / "中文名.txt").write_text("hi")
    (src / "子目录" / "data.bin").write_bytes(b"\x01")

    iso_path = str(tmp_path / "joliet.iso")
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3)             # 不开 Rock Ridge
    iso.add_directory("/___", joliet_path="/子目录")
    iso.add_file(str(src / "中文名.txt"), iso_path="/____.TXT;1", joliet_path="/中文名.txt")
    iso.add_file(str(src / "子目录" / "data.bin"), iso_path="/___/DATA.BIN;1",
                 joliet_path="/子目录/data.bin")
    iso.write(iso_path)
    iso.close()

    dest = tmp_path / "out"
    discimage.extract_iso(iso_path, str(dest))
    assert (dest / "中文名.txt").read_text() == "hi"
    assert (dest / "子目录" / "data.bin").read_bytes() == b"\x01"
