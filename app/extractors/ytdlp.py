"""yt-dlp：影片類內容的主力（TikTok、FB、小紅書、抖音，也是 IG/X 的備援）。"""

from __future__ import annotations

import logging

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from ..errors import MediaHookError
from .base import ExtractContext, ExtractResult, classify_error, collect_files, first_text

NAME = "yt-dlp"

log = logging.getLogger("mediahook.ytdlp")


class _Logger:
    """把 yt-dlp 的輸出接到 worker log，同時保留最近的訊息供判斷錯誤。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def _keep(self, msg: str) -> None:
        self.lines = [*self.lines[-199:], msg]

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.info(msg)
            self._keep(msg)

    def info(self, msg: str) -> None:
        self.debug(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)
        self._keep(msg)

    def error(self, msg: str) -> None:
        log.error(msg)
        self._keep(msg)


def build_options(ctx: ExtractContext, logger: _Logger) -> dict:
    options = {
        "logger": logger,
        "noprogress": True,
        "outtmpl": {"default": str(ctx.raw_dir / "%(autonumber)03d.%(ext)s")},
        "allowed_extractors": ["default", "-generic"],  # 不讓 yt-dlp 去抓任意網頁
        "playlist_items": f"1:{ctx.max_items}",
        "max_filesize": ctx.max_bytes,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
        "overwrites": True,
        "merge_output_format": "mp4",
    }
    if ctx.mode == "audio":
        options["format"] = "ba/b"
        options["format_sort"] = ["acodec:aac"]
    else:
        # 優先挑 iPhone 原生支援的 H.264 + AAC，省掉轉檔
        options["format"] = "bv*+ba/b"
        options["format_sort"] = ["vcodec:h264", "res", "acodec:aac"]
    if ctx.cookies:
        options["cookiefile"] = str(ctx.cookies)
    if ctx.proxy:
        options["proxy"] = ctx.proxy
    return options


def extract(ctx: ExtractContext) -> ExtractResult:
    logger = _Logger()
    try:
        with YoutubeDL(build_options(ctx, logger)) as ydl:
            info = ydl.extract_info(ctx.url, download=True) or {}
    except DownloadError as exc:
        raise classify_error(str(exc), ctx) from exc

    files = collect_files(ctx.raw_dir)
    if not files:
        if any("max-filesize" in line for line in logger.lines):
            raise MediaHookError("FILE_TOO_LARGE", limit=ctx.max_bytes // (1024 * 1024))
        raise MediaHookError("NO_MEDIA")

    entries = [entry for entry in (info.get("entries") or [info]) if entry]
    if ctx.mode == "auto" and all(entry.get("vcodec") == "none" for entry in entries):
        # 只抓到音訊（例如 TikTok 圖文），交給下一個工具抓圖片
        raise MediaHookError("NO_MEDIA")

    first = entries[0] if entries else {}
    return ExtractResult(
        files=files,
        caption=first_text(info.get("description"), first.get("description")),
        author=first_text(
            info.get("uploader"), info.get("channel"), first.get("uploader"), first.get("channel"),
            info.get("uploader_id"), first.get("uploader_id"),
        ),
        title=first_text(info.get("title"), first.get("title")),
        post_id=first_text(info.get("id"), first.get("id")),
    )
