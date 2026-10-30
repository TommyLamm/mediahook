"""HTTP API，給 iPhone 捷徑呼叫。

啟動：uvicorn --factory app.main:create_app
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import mimetypes
import re
import shutil
import time
from collections import Counter
from contextlib import asynccontextmanager
from importlib import metadata
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__
from .config import Settings
from .db import ACTIVE, Job, JobStore
from .errors import MediaHookError
from .jobs import JobManager
from .platforms import PLATFORMS, check_url, extract_url
from .signing import sign, verify

POLL_WAIT_SECONDS = 20

HTTP_STATUS = {
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "JOB_NOT_FOUND": 404,
    "FILE_NOT_FOUND": 404,
    "INVALID_REQUEST": 422,
    "QUEUE_FULL": 429,
}

# 對外只有三種狀態，捷徑判斷比較簡單
API_STATUS = {"queued": "running", "running": "running", "done": "done", "failed": "failed", "expired": "failed"}

KIND_LABELS = {"image": "張圖片", "video": "部影片", "audio": "個音訊檔"}

_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,32}")
_FILENAME_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}")

mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("audio/mp4", ".m4a")


class JobRequest(BaseModel):
    url: str
    mode: Literal["auto", "audio"] = "auto"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if len(settings.api_key) < 16:
        raise RuntimeError("請設定 MEDIAHOOK_API_KEY（至少 16 個字元，可用 openssl rand -hex 32 產生）")
    _setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        settings.cookies_dir.mkdir(parents=True, exist_ok=True)
        store = JobStore(settings.db_path)
        manager = JobManager(settings, store)
        manager.recover()
        app.state.store = store
        app.state.manager = manager
        cleanup = asyncio.create_task(manager.cleanup_loop())
        try:
            yield
        finally:
            cleanup.cancel()
            await manager.shutdown()
            store.close()

    app = FastAPI(title="MediaHook", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    def require_api_key(request: Request) -> None:
        auth = request.headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            token = auth[7:].strip()
        else:
            token = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(token.encode(), settings.api_key.encode()):
            raise MediaHookError("UNAUTHORIZED")

    def job_body(job: Job, request: Request) -> dict:
        base = settings.public_url or str(request.base_url).rstrip("/")
        body = {
            "id": job.id,
            "status": API_STATUS[job.status],
            "platform": job.platform,
            "poll_url": f"{base}/api/jobs/{job.id}?wait={POLL_WAIT_SECONDS}",
        }
        if job.status in ACTIVE:
            body["message"] = "處理中…"
        elif job.status == "done":
            body.update(_done_fields(job, base, settings))
        elif job.status == "expired":
            error = MediaHookError("EXPIRED")
            body.update(error_code=error.code, message=error.message)
        else:
            body.update(error_code=job.error_code, message=job.error_message)
        return body

    @app.exception_handler(MediaHookError)
    async def handle_error(request: Request, exc: MediaHookError) -> JSONResponse:
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(MediaHookError("INVALID_REQUEST"))

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/api/jobs", dependencies=[Depends(require_api_key)])
    async def create_job(body: JobRequest, request: Request, wait: float = Query(0, ge=0)) -> dict:
        url, platform = check_url(extract_url(body.url))
        manager: JobManager = request.app.state.manager
        job = manager.submit(url, body.mode, platform)
        await manager.wait(job.id, min(wait, settings.max_wait_seconds))
        return job_body(request.app.state.store.get(job.id), request)

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(require_api_key)])
    async def get_job(job_id: str, request: Request, wait: float = Query(0, ge=0)) -> dict:
        store: JobStore = request.app.state.store
        job = store.get(job_id)
        if job is None:
            raise MediaHookError("JOB_NOT_FOUND")
        if job.status in ACTIVE:
            await request.app.state.manager.wait(job_id, min(wait, settings.max_wait_seconds))
            job = store.get(job_id)
        return job_body(job, request)

    @app.get("/api/status", dependencies=[Depends(require_api_key)])
    async def status(request: Request) -> dict:
        return {
            "version": __version__,
            "tools": {name: _package_version(name) for name in ("yt-dlp", "gallery-dl")},
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "cookies": {p: (settings.cookies_dir / f"{p}.txt").is_file() for p in PLATFORMS},
            "proxies": sorted(settings.proxies),
            "active_jobs": request.app.state.store.count_active(),
        }

    @app.api_route("/f/{job_id}/{filename}", methods=["GET", "HEAD"])
    async def download(job_id: str, filename: str, exp: int = 0, sig: str = "") -> FileResponse:
        if not (_ID_RE.fullmatch(job_id) and _FILENAME_RE.fullmatch(filename)
                and verify(settings.signing_key, job_id, filename, exp, sig)):
            raise MediaHookError("FORBIDDEN")
        path = settings.jobs_dir / job_id / "files" / filename
        if not path.is_file():
            raise MediaHookError("FILE_NOT_FOUND")
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, filename=filename)

    return app


def _done_fields(job: Job, base: str, settings: Settings) -> dict:
    result = job.result or {}
    expires = int(time.time()) + settings.link_ttl_seconds
    items = []
    for file in result.get("files", []):
        name = file["name"]
        signature = sign(settings.signing_key, job.id, name, expires)
        items.append({
            "type": file["kind"],
            "url": f"{base}/f/{job.id}/{name}?exp={expires}&sig={signature}",
            "filename": name,
            "size": file["size"],
        })

    counts = Counter(item["type"] for item in items)
    summary = "、".join(f"{counts[kind]} {label}" for kind, label in KIND_LABELS.items() if counts[kind])
    caption = result.get("caption") or ""
    author = result.get("author") or ""
    source_url = result.get("url") or job.url
    return {
        "message": f"已完成：{summary}",
        "source_url": source_url,
        "author": author,
        "title": result.get("title") or "",
        "caption": caption,
        "share_text": _share_text(caption, author, source_url),
        "count": len(items),
        "items": items,
    }


def _share_text(caption: str, author: str, source_url: str) -> str:
    footer = "\n".join(line for line in (f"— {author}" if author else "", source_url) if line)
    return f"{caption.strip()}\n\n{footer}" if caption.strip() else footer


def _error_response(error: MediaHookError) -> JSONResponse:
    return JSONResponse(
        {"status": "failed", "error_code": error.code, "message": error.message},
        status_code=HTTP_STATUS.get(error.code, 400),
    )


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _setup_logging() -> None:
    logger = logging.getLogger("mediahook")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
