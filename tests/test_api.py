"""API 路由冒烟测试（Flask test client；不触发真实转换，快速且确定性）。"""
from __future__ import annotations

import threading
import time

import pytest

from vconv import config, create_app, ffmpeg_util, native_dialog


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个测试独立的 VCONV_DATA_DIR，避免污染真实/共享配置。"""
    monkeypatch.setenv("VCONV_DATA_DIR", str(tmp_path))
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "VConv" in r.get_data(as_text=True)
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_ffmpeg_status_shape(client):
    data = client.get("/api/ffmpeg").get_json()
    assert data["status"] in ("ok", "missing")


def test_hwaccel_shape(client):
    data = client.get("/api/hwaccel").get_json()
    assert isinstance(data["available"], dict)


def test_settings_roundtrip(client):
    r = client.put("/api/settings", json={"workers": 2, "default_output_dir": "/tmp"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["workers"] == 2
    assert body["default_output_dir"] == "/tmp"
    # 持久化生效
    assert client.get("/api/settings").get_json()["workers"] == 2


def test_settings_validation(client):
    assert client.put("/api/settings", json={"workers": 99}).status_code == 422
    assert client.put("/api/settings", json={"workers": "x"}).status_code == 422
    assert client.put("/api/settings", json={"default_output_dir": 123}).status_code == 422
    assert client.put("/api/settings", json={"ffmpeg_path": "/no/such/ffmpeg"}).status_code == 422


def test_jobs_validation_errors(client):
    # 空文件列表
    r = client.post("/api/jobs", json={"inputs": [], "settings": {}})
    assert r.status_code == 422
    assert "文件" in r.get_json()["error"]
    # 不存在的文件
    r = client.post("/api/jobs", json={"inputs": ["/no/such/file.mp4"], "settings": {}})
    assert r.status_code == 422
    assert "文件不存在" in r.get_json()["error"]
    # 非法编码/容器组合（校验先于文件检查，无需真实文件）
    r = client.post("/api/jobs", json={
        "inputs": ["/no/such/file.mp4"],
        "settings": {"video_codec": "h264", "container": "webm"},
    })
    assert r.status_code == 422
    assert "webm" in r.get_json()["error"]


def test_cancel_and_delete_404(client):
    assert client.post("/api/jobs/nonexistent/cancel").status_code == 404
    assert client.delete("/api/jobs/nonexistent").status_code == 404


def test_probe_missing_file(client):
    r = client.post("/api/probe", json={"path": "/no/such/file.mp4"})
    assert r.status_code == 422
    assert "文件不存在" in r.get_json()["error"]


def _wait_dl_idle(client, timeout=5.0):
    """轮询等待下载管理器回到空闲。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get("/api/ffmpeg").get_json()["downloading"]
        if not st["active"]:
            return st
        time.sleep(0.05)
    raise AssertionError("下载管理器未在 {} 秒内空闲".format(timeout))


def test_ffmpeg_download_flow_and_concurrency(client, monkeypatch):
    gate = threading.Event()
    seen = []

    def fake_download(progress_cb=None):
        seen.append(progress_cb)
        if progress_cb:
            progress_cb(42, "下载中")
        gate.wait(timeout=5)    # 阻塞直至测试放行
        return "/fake/ffmpeg"

    monkeypatch.setattr(ffmpeg_util, "download_ffmpeg", fake_download)

    assert client.post("/api/ffmpeg/download").status_code == 202
    # 并发门：已在下 → 409
    r = client.post("/api/ffmpeg/download")
    assert r.status_code == 409
    assert "进行中" in r.get_json()["error"]

    st = client.get("/api/ffmpeg").get_json()["downloading"]
    assert st["active"] is True
    assert st["percent"] == 42
    assert st["stage"] == "下载中"
    assert len(seen) == 1 and seen[0] is not None    # 进度回调已挂上

    gate.set()
    st = _wait_dl_idle(client)
    assert st["finished"] is True
    assert st["error"] == ""
    # 完成后可再次启动
    assert client.post("/api/ffmpeg/download").status_code == 202
    _wait_dl_idle(client)


def test_ffmpeg_download_error_surface(client, monkeypatch):
    def fake_download(progress_cb=None):
        raise RuntimeError("网络超时，请检查代理后重试")

    monkeypatch.setattr(ffmpeg_util, "download_ffmpeg", fake_download)
    assert client.post("/api/ffmpeg/download").status_code == 202
    st = _wait_dl_idle(client)
    assert st["finished"] is True
    assert "网络超时" in st["error"]


def test_settings_persist_to_disk(client):
    client.put("/api/settings", json={"workers": 3, "default_output_dir": "/tmp/out"})
    cfg = config.load_config()
    assert cfg["workers"] == 3
    assert cfg["default_output_dir"] == "/tmp/out"


def test_pick_files_bridge(client, monkeypatch):
    calls = []

    def fake_pick(folder=False):
        calls.append(folder)
        return ["/fake/a.mp4", "/fake/b.mp4"] if not folder else ["/fake/out"]

    monkeypatch.setattr(native_dialog, "pick_files", fake_pick)
    r = client.post("/api/pick-files", json={"folder": False})
    assert r.status_code == 200
    assert r.get_json()["paths"] == ["/fake/a.mp4", "/fake/b.mp4"]
    assert calls == [False]

    r = client.post("/api/pick-files", json={"folder": True})
    assert r.get_json()["paths"] == ["/fake/out"]
    assert calls == [False, True]


# ---- 磁盘镜像 ----

def _wait_disc_terminal(client, task_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t in client.get("/api/disc").get_json()["tasks"]:
            if t["id"] == task_id and t["status"] in ("done", "failed", "cancelled"):
                return t
        time.sleep(0.02)
    raise AssertionError("镜像任务未在 {} 秒内结束".format(timeout))


def test_disc_status_shape(client):
    data = client.get("/api/disc").get_json()
    assert data["platform"] in ("mac", "windows", "other")
    assert isinstance(data["create"], list)
    assert isinstance(data["extract"], list)
    assert isinstance(data["tasks"], list)


def test_disc_validation_errors(client):
    r = client.post("/api/disc/pack", json={"source_dir": "/no/such/dir", "fmt": "iso"})
    assert r.status_code == 422
    assert "源文件夹不存在" in r.get_json()["error"]

    r = client.post("/api/disc/pack", json={"source_dir": "/tmp", "fmt": "dmg"})
    assert r.status_code == 422
    assert "不支持" in r.get_json()["error"]

    r = client.post("/api/disc/extract", json={"image_path": "/no/such.iso"})
    assert r.status_code == 422
    assert "镜像文件不存在" in r.get_json()["error"]

    assert client.post("/api/disc/nope/cancel").status_code == 404
    assert client.delete("/api/disc/nope").status_code == 404


def test_disc_pack_flow(client, monkeypatch, tmp_path):
    from vconv import discimage

    src = tmp_path / "src"
    src.mkdir()

    monkeypatch.setattr(discimage._manager, "_run_pack",
                        lambda task: discimage._manager._finish(task, "done"))
    r = client.post("/api/disc/pack", json={"source_dir": str(src), "fmt": "iso"})
    assert r.status_code == 201
    task_id = r.get_json()["id"]

    t = _wait_disc_terminal(client, task_id)
    assert t["status"] == "done"
    assert t["progress"] == 100.0
    assert t["dest"].endswith("src.iso")

    assert client.delete("/api/disc/" + task_id).status_code == 204
    assert client.get("/api/disc").get_json()["tasks"] == []


def test_disc_extract_rejects_bad_ext(client, tmp_path):
    f = tmp_path / "x.exe"
    f.write_text("x")
    r = client.post("/api/disc/extract", json={"image_path": str(f)})
    assert r.status_code == 422
    assert "不支持的镜像格式" in r.get_json()["error"]
