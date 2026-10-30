import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

API_KEY = "test-key-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
FAKE_WORKER = (sys.executable, str(Path(__file__).with_name("fake_worker.py")))


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def factory(**overrides):
        settings = Settings(
            api_key=API_KEY,
            data_dir=tmp_path,
            worker_cmd=FAKE_WORKER,
            platform_min_interval=0,
            **overrides,
        )
        client = TestClient(create_app(settings))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def post_job(client, url, wait=10, **body):
    return client.post(f"/api/jobs?wait={wait}", json={"url": url, **body}, headers=AUTH)


def test_requires_api_key(client):
    response = client.post("/api/jobs", json={"url": "https://www.instagram.com/p/ABC/"})
    assert response.status_code == 401
    assert response.json() == {"status": "failed", "error_code": "UNAUTHORIZED", "message": "API 金鑰錯誤"}

    response = client.post("/api/jobs", json={"url": "x"}, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_x_api_key_header(client):
    response = client.post("/api/jobs?wait=10", json={"url": "https://www.instagram.com/p/ABC/"},
                           headers={"X-API-Key": API_KEY})
    assert response.json()["status"] == "done"


def test_rejects_unsupported_url(client):
    response = post_job(client, "https://www.youtube.com/watch?v=1")
    assert response.status_code == 400
    assert response.json()["error_code"] == "UNSUPPORTED_URL"


def test_rejects_bad_body(client):
    response = client.post("/api/jobs", json={"link": "x"}, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"

    response = post_job(client, "https://www.instagram.com/p/ABC/", mode="video")
    assert response.json()["error_code"] == "INVALID_REQUEST"


def test_full_flow(client):
    response = post_job(client, "分享自 Instagram：https://www.instagram.com/p/ABC/?igsh=1 ")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["message"] == "已完成：1 張圖片、1 部影片"
    assert body["caption"] == "今天的晚餐"
    assert body["share_text"] == "今天的晚餐\n\n— someone\nhttps://www.instagram.com/p/ABC/?igsh=1"
    assert body["count"] == 2
    assert [item["type"] for item in body["items"]] == ["image", "video"]

    # 下載連結不需要金鑰
    image = client.get(body["items"][0]["url"])
    assert image.status_code == 200
    assert image.content == b"jpeg-bytes"
    assert image.headers["content-type"] == "image/jpeg"
    video = client.get(body["items"][1]["url"])
    assert video.headers["content-type"] == "video/mp4"

    # 之後查詢同一個任務也拿得到結果
    again = client.get(f"/api/jobs/{body['id']}", headers=AUTH).json()
    assert again["status"] == "done" and again["count"] == 2


def test_long_poll(client):
    body = post_job(client, "https://www.instagram.com/p/ABC/", wait=0).json()
    assert body["status"] == "running"
    assert body["poll_url"].endswith(f"/api/jobs/{body['id']}?wait=20")
    polled = client.get(f"/api/jobs/{body['id']}?wait=10", headers=AUTH).json()
    assert polled["status"] == "done"


def test_failed_job(client):
    body = post_job(client, "https://www.instagram.com/p/fail/").json()
    assert body["status"] == "failed"
    assert body["error_code"] == "LOGIN_REQUIRED"
    assert "cookies" in body["message"]


def test_crashed_worker(client):
    body = post_job(client, "https://www.instagram.com/p/crash/").json()
    assert body["status"] == "failed"
    assert body["error_code"] == "EXTRACT_FAILED"


def test_timeout(make_client):
    client = make_client(job_timeout_seconds=1)
    started = time.monotonic()
    body = post_job(client, "https://www.instagram.com/p/sleep/").json()
    assert body["status"] == "failed"
    assert body["error_code"] == "TIMEOUT"
    assert time.monotonic() - started < 10


def test_queue_full(make_client):
    client = make_client(max_pending_jobs=1, max_concurrent_jobs=1, job_timeout_seconds=5)
    assert post_job(client, "https://www.instagram.com/p/sleep/", wait=0).json()["status"] == "running"
    response = post_job(client, "https://www.instagram.com/p/ABC/", wait=0)
    assert response.status_code == 429
    assert response.json()["error_code"] == "QUEUE_FULL"


def test_unknown_job(client):
    response = client.get("/api/jobs/doesnotexist", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error_code"] == "JOB_NOT_FOUND"


def test_file_link_tampering(client):
    body = post_job(client, "https://www.instagram.com/p/ABC/").json()
    url = body["items"][0]["url"]
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    path = parts.path

    assert client.get(f"{path}?exp={query['exp'][0]}&sig={'0' * 32}").status_code == 403
    assert client.get(f"{path}?exp={int(query['exp'][0]) + 1}&sig={query['sig'][0]}").status_code == 403
    other = path.replace("_01.jpg", "_02.mp4")
    assert client.get(f"{other}?exp={query['exp'][0]}&sig={query['sig'][0]}").status_code == 403
    assert client.get(path).status_code == 403


def test_public_url_in_links(make_client):
    client = make_client(public_url="https://dl.gyke.net")
    body = post_job(client, "https://www.instagram.com/p/ABC/").json()
    assert body["items"][0]["url"].startswith(f"https://dl.gyke.net/f/{body['id']}/")
    assert body["poll_url"].startswith("https://dl.gyke.net/api/jobs/")


def test_cleanup_expires_files(client):
    body = post_job(client, "https://www.instagram.com/p/ABC/").json()
    manager = client.app.state.manager
    assert manager.cleanup(now=time.time() + 7 * 3600) == 1

    expired = client.get(f"/api/jobs/{body['id']}", headers=AUTH).json()
    assert expired["status"] == "failed" and expired["error_code"] == "EXPIRED"
    assert client.get(body["items"][0]["url"]).status_code == 404
    assert not (manager.settings.jobs_dir / body["id"]).exists()


def test_status_endpoint(client, tmp_path):
    (tmp_path / "cookies" / "instagram.txt").write_text("# Netscape HTTP Cookie File\n")
    status = client.get("/api/status", headers=AUTH).json()
    assert status["cookies"]["instagram"] is True
    assert status["cookies"]["tiktok"] is False
    assert status["active_jobs"] == 0
    assert client.get("/api/status").status_code == 401


def test_health_is_public(client):
    assert client.get("/health").json() == {"ok": True}


def test_restart_marks_running_jobs_interrupted(tmp_path):
    settings = Settings(api_key=API_KEY, data_dir=tmp_path, worker_cmd=FAKE_WORKER, platform_min_interval=0)
    with TestClient(create_app(settings)) as first:
        job_id = post_job(first, "https://www.instagram.com/p/sleep/", wait=0).json()["id"]
    with TestClient(create_app(settings)) as second:
        body = second.get(f"/api/jobs/{job_id}", headers=AUTH).json()
    assert body["status"] == "failed" and body["error_code"] == "INTERRUPTED"


def test_rejects_short_api_key(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(Settings(api_key="short", data_dir=tmp_path))
