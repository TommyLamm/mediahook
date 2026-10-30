"""各下載工具共用的資料結構與錯誤分類。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import MediaHookError
from ..platforms import DISPLAY_NAMES

MEDIA_SUFFIXES = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic", ".heif", ".bmp", ".tif", ".tiff", ".jfif",
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".flv", ".ts", ".3gp",
    ".m4a", ".mp3", ".aac", ".opus", ".ogg", ".oga", ".wav", ".flac", ".weba",
})


@dataclass
class ExtractContext:
    url: str
    platform: str
    mode: str  # auto | audio
    raw_dir: Path
    cookies: Path | None = None
    proxy: str | None = None
    max_bytes: int = 1024 * 1024 * 1024
    max_items: int = 30

    @property
    def platform_name(self) -> str:
        return DISPLAY_NAMES.get(self.platform, self.platform)


@dataclass
class ExtractResult:
    files: list[Path]
    caption: str | None = None
    author: str | None = None
    title: str | None = None
    post_id: str | None = None


def collect_files(directory: Path) -> list[Path]:
    """依檔名排序列出下載好的媒體檔（.part 等暫存檔會被排除）。"""
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES)


def first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def pick(data: dict, *paths: str) -> str | None:
    """依序嘗試 'a.b' 形式的路徑，回傳第一個有值的文字。"""
    values = []
    for path in paths:
        value: Any = data
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        values.append(value)
    return first_text(*values)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_PREFIX_RE = re.compile(r"^(?:ERROR:\s*)?(?:\[[^\]]+\]\s*)?(?:[\w-]+:\s+)?")

_LOGIN_HINTS = (
    "login required", "log in", "login page", "requires authentication", "authenticationerror",
    "authorizationerror", "credentials", "cookies", "sign in", "not logged in", "private",
)
_RATE_LIMIT_HINTS = ("429", "too many requests", "rate limit", "rate-limit", "ratelimit")
_NOT_FOUND_HINTS = (
    "404", "not found", "could not be found", "does not exist", "has been removed",
    "deleted", "unavailable", "isn't available", "not available",
)
_NO_MEDIA_HINTS = ("no video", "no media", "no formats", "no images")
_NETWORK_HINTS = (
    "proxyerror", "connection refused", "connection reset", "connectionerror", "timed out",
    "name resolution", "name or service not known", "network is unreachable", "tunnel failed",
)


def classify_error(text: str, ctx: ExtractContext) -> MediaHookError:
    """把下載工具的錯誤訊息轉成我們的錯誤碼。"""
    lowered = text.lower()
    if any(hint in lowered for hint in _LOGIN_HINTS):
        return MediaHookError("COOKIES_EXPIRED" if ctx.cookies else "LOGIN_REQUIRED", platform=ctx.platform_name)
    if any(hint in lowered for hint in _RATE_LIMIT_HINTS):
        return MediaHookError("RATE_LIMITED", platform=ctx.platform_name)
    if any(hint in lowered for hint in _NETWORK_HINTS):
        return MediaHookError("NETWORK_ERROR", platform=ctx.platform_name)
    if "unsupported url" in lowered:
        return MediaHookError("UNSUPPORTED_LINK", platform=ctx.platform_name)
    if any(hint in lowered for hint in _NOT_FOUND_HINTS):
        return MediaHookError("NOT_FOUND")
    if any(hint in lowered for hint in _NO_MEDIA_HINTS):
        return MediaHookError("NO_MEDIA")
    return MediaHookError("EXTRACT_FAILED", detail=clean_message(text))


def clean_message(text: str, limit: int = 200) -> str:
    line = _ANSI_RE.sub("", text).strip().splitlines()[0] if text.strip() else "未知錯誤"
    line = _PREFIX_RE.sub("", line, count=1)
    for noise in ("; please report this issue", " (caused by ", ". See https://"):
        line = line.split(noise)[0]
    return line.strip()[:limit] or "未知錯誤"
