"""支援的平台、網址白名單與正規化（純字串處理，不連網）。"""

from __future__ import annotations

import re
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .errors import MediaHookError

PLATFORMS: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram.com", "instagr.am"),
    "threads": ("threads.net", "threads.com"),
    "tiktok": ("tiktok.com",),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "twitter": (
        "x.com", "twitter.com", "t.co",
        "fxtwitter.com", "vxtwitter.com", "fixupx.com", "fixvx.com",
    ),
    "facebook": ("facebook.com", "fb.com", "fb.watch"),
}

DISPLAY_NAMES = {
    "instagram": "Instagram",
    "threads": "Threads",
    "tiktok": "TikTok",
    "douyin": "抖音",
    "xiaohongshu": "小紅書",
    "twitter": "X",
    "facebook": "Facebook",
}

# 需要先跟著轉址才知道真正貼文網址的短網址
SHORTLINK_HOSTS = frozenset({
    "vm.tiktok.com", "vt.tiktok.com", "v.douyin.com",
    "xhslink.com", "www.xhslink.com", "t.co", "fb.watch",
})
SHORTLINK_PATHS = {
    "tiktok": ("/t/",),
    "facebook": ("/share/",),
    "instagram": ("/share/",),
}

_TWITTER_ALIASES = frozenset({
    "twitter.com", "www.twitter.com", "mobile.twitter.com", "mobile.x.com", "www.x.com",
    "fxtwitter.com", "vxtwitter.com", "fixupx.com", "fixvx.com",
})

# 只收 RFC 3986 的網址字元：抖音、小紅書的分享文字常在網址後面直接接中文
_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?)]}'\""


def extract_url(text: str) -> str:
    """從分享文字中取出第一個網址。"""
    match = _URL_RE.search(text or "")
    if not match:
        raise MediaHookError("INVALID_URL")
    return match.group(0).rstrip(_TRAILING_PUNCT)


def platform_of(host: str) -> str | None:
    host = host.lower().rstrip(".")
    for platform, domains in PLATFORMS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return platform
    return None


def check_url(url: str) -> tuple[str, str]:
    """驗證網址在白名單內，回傳 (正規化後的網址, 平台)。"""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise MediaHookError("INVALID_URL") from None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise MediaHookError("INVALID_URL")
    platform = platform_of(parts.hostname)
    if platform is None or parts.username or parts.password or port not in (None, 80, 443):
        raise MediaHookError("UNSUPPORTED_URL", host=parts.hostname)
    return _canonicalize(parts), platform


def needs_expansion(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in SHORTLINK_HOSTS:
        return True
    prefixes = SHORTLINK_PATHS.get(platform_of(host) or "", ())
    return parts.path.startswith(prefixes)


def _canonicalize(parts: SplitResult) -> str:
    host = parts.hostname.lower()
    path, query = parts.path or "/", parts.query

    if host in _TWITTER_ALIASES:
        host = "x.com"
    elif host == "instagr.am":
        host = "www.instagram.com"
    elif host.endswith("iesdouyin.com"):
        # v.douyin.com 展開後會到 iesdouyin.com/share/video/<id>，轉成 yt-dlp 認得的格式
        if match := re.match(r"/share/(video|note|slides)/(\d+)", path):
            kind = "video" if match[1] == "video" else "note"
            host, path, query = "www.douyin.com", f"/{kind}/{match[2]}", ""
    elif host in ("douyin.com", "www.douyin.com"):
        if match := re.search(r"(?:^|&)modal_id=(\d+)", query):
            path, query = f"/video/{match[1]}", ""

    return urlunsplit(("https", host, path, query, ""))
