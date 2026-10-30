"""任務佇列：每個下載在獨立子程序執行（可逾時強制中止），並支援長輪詢。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import time
from collections import defaultdict

from .config import Settings
from .db import ACTIVE, Job, JobStore
from .errors import MediaHookError

log = logging.getLogger("mediahook.jobs")

CLEANUP_INTERVAL_SECONDS = 600


def _failed(error: MediaHookError) -> dict:
    return {"status": "failed", "error_code": error.code, "error_message": error.message}


def _kill(proc: asyncio.subprocess.Process) -> None:
    # worker 用 start_new_session 啟動，整個程序群組一起砍，ffmpeg 子程序才不會殘留
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)


class JobManager:
    def __init__(self, settings: Settings, store: JobStore) -> None:
        self.settings = settings
        self.store = store
        self._slots = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._finished: dict[str, asyncio.Event] = {}
        self._tasks: set[asyncio.Task] = set()
        self._platform_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._platform_last: dict[str, float] = {}

    def recover(self) -> None:
        error = MediaHookError("INTERRUPTED")
        if count := self.store.fail_active(error.code, error.message):
            log.warning("%d 個任務因服務重啟而中斷", count)

    def submit(self, url: str, mode: str, platform: str) -> Job:
        if self.store.count_active() >= self.settings.max_pending_jobs:
            raise MediaHookError("QUEUE_FULL")
        job = self.store.create(url, mode, platform)
        self._finished[job.id] = asyncio.Event()
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        log.info("任務 %s 建立：%s（%s）", job.id, url, mode)
        return job

    async def wait(self, job_id: str, timeout: float) -> None:
        """等到任務結束或逾時（長輪詢用）。"""
        event = self._finished.get(job_id)
        if event is None or timeout <= 0:
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(event.wait(), timeout)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(self, job: Job) -> None:
        try:
            async with self._slots:
                await self._throttle(job.platform)
                self.store.update(job.id, status="running")
                outcome = await self._execute(job)
            self.store.update(job.id, **outcome)
            if outcome["status"] == "done":
                log.info("任務 %s 完成", job.id)
            else:
                log.warning("任務 %s 失敗：%s %s", job.id, outcome["error_code"], outcome["error_message"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("任務 %s 發生未預期錯誤", job.id)
            self.store.update(job.id, **_failed(MediaHookError("EXTRACT_FAILED", detail="伺服器內部錯誤")))
        finally:
            if event := self._finished.pop(job.id, None):
                event.set()

    async def _throttle(self, platform: str) -> None:
        """同一平台的任務之間至少間隔 platform_min_interval 秒，降低被風控的機率。"""
        interval = self.settings.platform_min_interval
        if interval <= 0:
            return
        async with self._platform_locks[platform]:
            delay = self._platform_last.get(platform, float("-inf")) + interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._platform_last[platform] = time.monotonic()

    async def _execute(self, job: Job) -> dict:
        settings = self.settings
        job_dir = settings.jobs_dir / job.id
        job_dir.mkdir(parents=True, exist_ok=True)
        request = {
            "url": job.url,
            "mode": job.mode,
            "cookies_dir": str(settings.cookies_dir),
            "proxies": dict(settings.proxies),
            "max_file_mb": settings.max_file_mb,
            "max_items": settings.max_items,
        }
        (job_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")

        with open(job_dir / "worker.log", "wb") as worker_log:
            proc = await asyncio.create_subprocess_exec(
                *settings.worker_cmd, str(job_dir),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=worker_log,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                await asyncio.wait_for(proc.wait(), settings.job_timeout_seconds)
            except asyncio.TimeoutError:
                _kill(proc)
                await proc.wait()
                return _failed(MediaHookError("TIMEOUT"))
            except asyncio.CancelledError:
                _kill(proc)
                raise

        try:
            result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _failed(MediaHookError("EXTRACT_FAILED", detail=f"worker 異常結束（exit {proc.returncode}）"))
        if result.get("ok"):
            return {"status": "done", "result": result}
        return {
            "status": "failed",
            "error_code": result.get("code", "EXTRACT_FAILED"),
            "error_message": result.get("message", ""),
        }

    async def cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.cleanup)
            except Exception:
                log.exception("清理過期檔案失敗")
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

    def cleanup(self, now: float | None = None) -> int:
        """刪除超過保存期限的任務檔案，回傳清掉的任務數。"""
        cutoff = (time.time() if now is None else now) - self.settings.file_ttl_hours * 3600
        expired = self.store.finished_before(cutoff)
        for job_id in expired:
            shutil.rmtree(self.settings.jobs_dir / job_id, ignore_errors=True)
            self.store.update(job_id, status="expired")

        # 資料庫裡已經沒有在跑的殘留資料夾也一併清掉
        for path in self.settings.jobs_dir.iterdir():
            if path.is_dir() and path.stat().st_mtime < cutoff:
                job = self.store.get(path.name)
                if job is None or job.status not in ACTIVE:
                    shutil.rmtree(path, ignore_errors=True)

        if expired:
            log.info("已清除 %d 個過期任務", len(expired))
        return len(expired)
