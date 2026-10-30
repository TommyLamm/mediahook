import pytest

from app.errors import MediaHookError
from app.platforms import check_url, extract_url, needs_expansion


@pytest.mark.parametrize("text, expected", [
    ("https://www.instagram.com/p/ABC123/", "https://www.instagram.com/p/ABC123/"),
    ("7.43 復制打開抖音，看看【某某的作品】好看 https://v.douyin.com/iRNBho6u/ 12/30 abc:/",
     "https://v.douyin.com/iRNBho6u/"),
    ("【標題】 http://xhslink.com/a/AbCdEf，複製本條信息，打開小紅書App查看",
     "http://xhslink.com/a/AbCdEf"),
    ("看這個 (https://x.com/user/status/123).", "https://x.com/user/status/123"),
    ("https://v.douyin.com/abc/复制此链接，打开Dou音搜索", "https://v.douyin.com/abc/"),
    ("'https://www.tiktok.com/@u/video/1?lang=zh-Hant'", "https://www.tiktok.com/@u/video/1?lang=zh-Hant"),
])
def test_extract_url(text, expected):
    assert extract_url(text) == expected


def test_extract_url_without_url():
    with pytest.raises(MediaHookError) as info:
        extract_url("沒有網址")
    assert info.value.code == "INVALID_URL"


@pytest.mark.parametrize("url, expected_url, platform", [
    ("https://www.instagram.com/p/ABC/?igsh=xyz", "https://www.instagram.com/p/ABC/?igsh=xyz", "instagram"),
    ("https://instagr.am/p/ABC/", "https://www.instagram.com/p/ABC/", "instagram"),
    ("https://www.threads.net/@user/post/ABC", "https://www.threads.net/@user/post/ABC", "threads"),
    ("https://vm.tiktok.com/ZMabc/", "https://vm.tiktok.com/ZMabc/", "tiktok"),
    ("https://twitter.com/user/status/1", "https://x.com/user/status/1", "twitter"),
    ("https://fxtwitter.com/user/status/1", "https://x.com/user/status/1", "twitter"),
    ("http://x.com/user/status/1#frag", "https://x.com/user/status/1", "twitter"),
    ("https://m.facebook.com/watch/?v=1", "https://m.facebook.com/watch/?v=1", "facebook"),
    ("https://www.iesdouyin.com/share/video/7300000000000000000/?region=CN",
     "https://www.douyin.com/video/7300000000000000000", "douyin"),
    ("https://www.iesdouyin.com/share/note/7300000000000000001/",
     "https://www.douyin.com/note/7300000000000000001", "douyin"),
    ("https://www.douyin.com/discover?modal_id=7300000000000000002",
     "https://www.douyin.com/video/7300000000000000002", "douyin"),
    ("https://www.xiaohongshu.com/explore/abc123?xsec_token=t",
     "https://www.xiaohongshu.com/explore/abc123?xsec_token=t", "xiaohongshu"),
])
def test_check_url(url, expected_url, platform):
    assert check_url(url) == (expected_url, platform)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=1",
    "https://instagram.com.evil.example/p/1",
    "https://evilinstagram.com/p/1",
    "https://user:pass@www.instagram.com/p/1",
    "https://www.instagram.com:8443/p/1",
    "http://127.0.0.1/",
])
def test_check_url_rejects(url):
    with pytest.raises(MediaHookError) as info:
        check_url(url)
    assert info.value.code == "UNSUPPORTED_URL"


@pytest.mark.parametrize("url", ["ftp://www.instagram.com/p/1", "https://", "https://[::1/"])
def test_check_url_invalid(url):
    with pytest.raises(MediaHookError) as info:
        check_url(url)
    assert info.value.code == "INVALID_URL"


@pytest.mark.parametrize("url, expected", [
    ("https://vm.tiktok.com/ZMabc/", True),
    ("https://www.tiktok.com/t/ZTabc/", True),
    ("https://v.douyin.com/abc/", True),
    ("http://xhslink.com/a/abc", True),
    ("https://t.co/abc", True),
    ("https://fb.watch/abc/", True),
    ("https://www.facebook.com/share/r/abc/", True),
    ("https://www.instagram.com/share/reel/abc/", True),
    ("https://www.tiktok.com/@u/video/1", False),
    ("https://www.instagram.com/p/abc/", False),
])
def test_needs_expansion(url, expected):
    assert needs_expansion(url) is expected
