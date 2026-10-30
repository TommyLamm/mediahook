from types import SimpleNamespace

import pytest

from app import extractors
from app.errors import MediaHookError
from app.extractors import gallerydl, ytdlp
from app.extractors.base import ExtractContext, ExtractResult, classify_error, clean_message, pick


def make_ctx(tmp_path, platform="instagram", cookies=None, url="https://www.instagram.com/p/x/"):
    return ExtractContext(url=url, platform=platform, mode="auto", raw_dir=tmp_path / "raw", cookies=cookies)


@pytest.mark.parametrize("message, code", [
    ("ERROR: [Instagram] ABC: Requested content is not available, rate-limit reached or login required. "
     "Use --cookies", "LOGIN_REQUIRED"),
    ("AuthorizationError: Account credentials required", "LOGIN_REQUIRED"),
    ("HttpError: '429 Too Many Requests' for 'https://x.com/i/api'", "RATE_LIMITED"),
    ("ERROR: [twitter] 1: HTTP Error 404: Not Found", "NOT_FOUND"),
    ("NotFoundError: Requested post could not be found", "NOT_FOUND"),
    ("ERROR: [Instagram] ABC: There's no video in this post", "NO_MEDIA"),
    ("ERROR: Unsupported URL: https://www.instagram.com/someone/", "UNSUPPORTED_LINK"),
    ("ERROR: [TikTok] 1: Unable to extract webpage video data", "EXTRACT_FAILED"),
    ("ERROR: [TikTok] 1: Unable to download webpage: Failed to perform, curl: (7) CONNECT tunnel failed, "
     "response 403 (caused by ProxyError('...'))", "NETWORK_ERROR"),
    ("Failed to extract post (HttpError:  ProxyError: 403 Forbidden)", "NETWORK_ERROR"),
])
def test_classify_error(tmp_path, message, code):
    assert classify_error(message, make_ctx(tmp_path)).code == code


def test_classify_login_with_cookies(tmp_path):
    error = classify_error("login required", make_ctx(tmp_path, cookies=tmp_path / "c.txt"))
    assert error.code == "COOKIES_EXPIRED"
    assert "Instagram" in error.message


def test_clean_message():
    text = "\x1b[0;31mERROR:\x1b[0m [TikTok] 123: Unable to extract data; please report this issue on https://..."
    assert clean_message(text) == "Unable to extract data"
    text = "ERROR: [x] 1: Unable to download: boom. See https://curl.se/x (caused by Foo('bar'))"
    assert clean_message(text) == "Unable to download: boom"


def test_pick():
    meta = {"author": {"name": "jack", "nick": "Jack"}, "content": "hello", "tweet_id": 20}
    assert pick(meta, "description", "content") == "hello"
    assert pick(meta, "username", "author.name") == "jack"
    assert pick(meta, "post_shortcode", "tweet_id") == "20"
    assert pick(meta, "missing.path") is None


def test_chain_for_tiktok_photo():
    assert extractors.chain_for("tiktok", "https://www.tiktok.com/@u/photo/1")[0] is gallerydl
    assert extractors.chain_for("tiktok", "https://www.tiktok.com/@u/video/1")[0] is ytdlp


def fake_extractor(name, outcome):
    def extract(ctx):
        if isinstance(outcome, Exception):
            raise outcome
        path = ctx.raw_dir / "001.jpg"
        path.write_bytes(b"x")
        return ExtractResult(files=[path], caption=name)
    return SimpleNamespace(NAME=name, extract=extract)


def test_run_chain_falls_back(tmp_path, monkeypatch):
    chain = (fake_extractor("a", MediaHookError("NO_MEDIA")), fake_extractor("b", None))
    monkeypatch.setattr(extractors, "chain_for", lambda platform, url: chain)
    result = extractors.run_chain(make_ctx(tmp_path))
    assert result.caption == "b"


def test_run_chain_reports_most_useful_error(tmp_path, monkeypatch):
    chain = (
        fake_extractor("a", MediaHookError("EXTRACT_FAILED", detail="boom")),
        fake_extractor("b", MediaHookError("LOGIN_REQUIRED", platform="Instagram")),
        fake_extractor("c", RuntimeError("bug")),
    )
    monkeypatch.setattr(extractors, "chain_for", lambda platform, url: chain)
    with pytest.raises(MediaHookError) as info:
        extractors.run_chain(make_ctx(tmp_path))
    assert info.value.code == "LOGIN_REQUIRED"


def test_run_chain_not_implemented(tmp_path):
    with pytest.raises(MediaHookError) as info:
        extractors.run_chain(make_ctx(tmp_path, platform="threads", url="https://www.threads.net/@u/post/x"))
    assert info.value.code == "NOT_IMPLEMENTED"
    assert "Threads" in info.value.message


def test_ytdlp_options(tmp_path):
    ctx = make_ctx(tmp_path, cookies=tmp_path / "c.txt")
    ctx.proxy = "socks5://127.0.0.1:1080"
    options = ytdlp.build_options(ctx, ytdlp._Logger())
    assert options["allowed_extractors"] == ["default", "-generic"]
    assert options["cookiefile"].endswith("c.txt")
    assert options["proxy"] == "socks5://127.0.0.1:1080"
    assert options["format"] == "bv*+ba/b"
    ctx.mode = "audio"
    assert ytdlp.build_options(ctx, ytdlp._Logger())["format"] == "ba/b"


def test_gallerydl_unsupported_link(tmp_path):
    ctx = make_ctx(tmp_path, url="https://www.instagram.com/")
    ctx.raw_dir.mkdir()
    ctx.url = "https://example.invalid/nothing"
    with pytest.raises(MediaHookError) as info:
        gallerydl.extract(ctx)
    assert info.value.code == "UNSUPPORTED_LINK"
