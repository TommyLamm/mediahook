"""用本機 HTTP 伺服器驗證跟 gallery-dl、requests 的實際整合（不連外網）。"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest
from PIL import Image

from app import resolve
from app.errors import MediaHookError
from app.extractors import gallerydl
from app.extractors.base import ExtractContext


def jpeg_bytes(color):
    buffer = BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, "JPEG")
    return buffer.getvalue()


ROUTES = {
    "/a.jpg": (200, {"Content-Type": "image/jpeg"}, jpeg_bytes("red")),
    "/to-instagram": (302, {"Location": "https://www.instagram.com/p/ABC/"}, b""),
    "/to-internal": (302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b""),
    "/no-redirect": (200, {"Content-Type": "text/html"}, b"<html></html>"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, headers, body = ROUTES.get(self.path, (404, {}, b""))
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1,localhost")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_gallerydl_collects_downloaded_files(server, tmp_path):
    ctx = ExtractContext(url=f"{server}/a.jpg", platform="instagram", mode="auto", raw_dir=tmp_path / "raw")
    ctx.raw_dir.mkdir()
    result = gallerydl.extract(ctx)
    assert len(result.files) == 1
    assert result.files[0].parent == ctx.raw_dir
    assert result.files[0].read_bytes() == ROUTES["/a.jpg"][2]


def test_gallerydl_http_error(server, tmp_path):
    ctx = ExtractContext(url=f"{server}/missing.jpg", platform="instagram", mode="auto", raw_dir=tmp_path / "raw")
    ctx.raw_dir.mkdir()
    with pytest.raises(MediaHookError) as info:
        gallerydl.extract(ctx)
    assert info.value.code == "NOT_FOUND"


@pytest.fixture
def local_shortlinks(monkeypatch, server):
    """把本機伺服器當成短網址服務。"""
    monkeypatch.setattr(resolve, "needs_expansion", lambda url: url.startswith(server))
    return server


def test_expand_follows_redirect_to_platform(local_shortlinks):
    assert resolve.expand_url(f"{local_shortlinks}/to-instagram") == "https://www.instagram.com/p/ABC/"


def test_expand_blocks_redirect_to_internal_address(local_shortlinks):
    with pytest.raises(MediaHookError) as info:
        resolve.expand_url(f"{local_shortlinks}/to-internal")
    assert info.value.code == "UNSUPPORTED_URL"


def test_expand_without_redirect_keeps_url(local_shortlinks):
    url = f"{local_shortlinks}/no-redirect"
    assert resolve.expand_url(url) == url
