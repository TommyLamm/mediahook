"""依平台決定下載工具的嘗試順序，前一個失敗就換下一個。"""

from __future__ import annotations

import logging
import shutil
from urllib.parse import urlsplit

from ..errors import MediaHookError
from . import gallerydl, ytdlp
from .base import ExtractContext, ExtractResult

__all__ = ["ExtractContext", "ExtractResult", "chain_for", "run_chain"]

log = logging.getLogger("mediahook.extractors")

CHAINS = {
    "instagram": (gallerydl, ytdlp),
    "twitter": (gallerydl, ytdlp),
    "tiktok": (ytdlp, gallerydl),
    "facebook": (ytdlp, gallerydl),
    "xiaohongshu": (ytdlp,),  # 圖文筆記：Phase 2 自寫 adapter
    "douyin": (ytdlp,),       # 圖文：Phase 2 自寫 adapter
    "threads": (),            # Phase 2 自寫 adapter
}

# 全部失敗時，回報「最有用」的那個錯誤
ERROR_PRIORITY = (
    "COOKIES_EXPIRED", "LOGIN_REQUIRED", "RATE_LIMITED", "NOT_FOUND", "FILE_TOO_LARGE",
    "NETWORK_ERROR", "NO_MEDIA", "EXTRACT_FAILED", "UNSUPPORTED_LINK",
)


def chain_for(platform: str, url: str) -> tuple:
    if platform == "tiktok" and "/photo/" in urlsplit(url).path:
        return (gallerydl, ytdlp)
    return CHAINS.get(platform, ())


def run_chain(ctx: ExtractContext) -> ExtractResult:
    extractors = chain_for(ctx.platform, ctx.url)
    if not extractors:
        raise MediaHookError("NOT_IMPLEMENTED", platform=ctx.platform_name)

    errors: list[MediaHookError] = []
    for extractor in extractors:
        shutil.rmtree(ctx.raw_dir, ignore_errors=True)
        ctx.raw_dir.mkdir(parents=True)
        try:
            result = extractor.extract(ctx)
        except MediaHookError as exc:
            log.warning("%s 失敗：%s %s", extractor.NAME, exc.code, exc.message)
            errors.append(exc)
            continue
        except Exception as exc:
            log.exception("%s 發生未預期錯誤", extractor.NAME)
            errors.append(MediaHookError("EXTRACT_FAILED", detail=f"{type(exc).__name__}: {exc}"[:200]))
            continue
        log.info("%s 下載了 %d 個檔案", extractor.NAME, len(result.files))
        return result

    raise min(errors, key=_priority)


def _priority(error: MediaHookError) -> int:
    return ERROR_PRIORITY.index(error.code) if error.code in ERROR_PRIORITY else len(ERROR_PRIORITY)
