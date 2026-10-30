"""gallery-dl：圖片與多圖貼文的主力（IG 輪播與限動、X 多圖、TikTok 圖文、FB 相片）。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from gallery_dl import config as gdl_config
from gallery_dl import exception as gdl_exception
from gallery_dl import job as gdl_job

from ..errors import MediaHookError
from .base import ExtractContext, ExtractResult, classify_error, pick

NAME = "gallery-dl"

log = logging.getLogger("mediahook.gallerydl")


class _Collector:
    def __init__(self, max_items: int) -> None:
        self.max_items = max_items
        self.files: list[Path] = []
        self.metadata: dict | None = None


class _CollectJob(gdl_job.DownloadJob):
    """照下載順序記錄檔案路徑，並保留第一筆貼文資料（文案、作者）。"""

    def __init__(self, extr, parent=None, collector: _Collector | None = None) -> None:
        super().__init__(extr, parent)
        self.collector = collector if collector is not None else parent.collector

    def handle_url(self, url, kwdict):
        collector = self.collector
        if len(collector.files) >= collector.max_items:
            raise gdl_exception.TerminateExtraction()
        if collector.metadata is None:
            collector.metadata = dict(kwdict)
        super().handle_url(url, kwdict)
        path = self.pathfmt.realpath
        if path and os.path.isfile(path) and Path(path) not in collector.files:
            collector.files.append(Path(path))


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _configure(ctx: ExtractContext) -> None:
    gdl_config.clear()  # 不讀任何使用者設定檔，所有設定都在這裡
    set_option = gdl_config.set
    set_option(("extractor",), "base-directory", str(ctx.raw_dir))
    set_option(("extractor",), "directory", [])
    set_option(("extractor",), "timeout", 30)
    set_option(("extractor",), "retries", 3)
    set_option(("extractor",), "cookies-update", False)
    if ctx.cookies:
        set_option(("extractor",), "cookies", str(ctx.cookies))
    if ctx.proxy:
        set_option(("extractor",), "proxy", ctx.proxy)
    set_option(("downloader",), "filesize-max", str(ctx.max_bytes))
    set_option(("downloader",), "mtime", False)
    set_option(("output",), "mode", "null")


def extract(ctx: ExtractContext) -> ExtractResult:
    _configure(ctx)
    collector = _Collector(ctx.max_items)
    capture = _LogCapture()
    root = logging.getLogger()
    root.addHandler(capture)
    try:
        status = _CollectJob(ctx.url, collector=collector).run()
    except gdl_exception.NoExtractorError:
        raise MediaHookError("UNSUPPORTED_LINK", platform=ctx.platform_name) from None
    except gdl_exception.TerminateExtraction:
        status = 0  # 達到數量上限
    finally:
        root.removeHandler(capture)

    if not collector.files:
        if status:
            raise classify_error(" ".join(capture.messages) or f"gallery-dl status {status}", ctx)
        raise MediaHookError("NO_MEDIA")
    if status:
        log.warning("部分檔案下載失敗（status %s），保留已下載的 %d 個", status, len(collector.files))

    meta = collector.metadata or {}
    return ExtractResult(
        files=collector.files,
        caption=pick(meta, "description", "content", "desc", "caption", "text", "message", "title"),
        author=pick(
            meta, "username", "author.name", "author.uniqueId", "author.nick", "author.nickname",
            "user.name", "user.nick", "owner.username", "uploader", "nickname",
        ),
        post_id=pick(meta, "post_shortcode", "shortcode", "tweet_id", "post_id", "id"),
    )
