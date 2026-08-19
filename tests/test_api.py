"""API 路由冒烟测试（Flask test client；不触发真实转换，快速且确定性）。"""
from __future__ import annotations

import pytest

from vconv import create_app, native_dialog


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
