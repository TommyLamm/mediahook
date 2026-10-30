"""服務設定，全部從環境變數（MEDIAHOOK_*）讀取。"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .platforms import PLATFORMS

ENV_PREFIX = "MEDIAHOOK_"


@dataclass(frozen=True)
class Settings:
    api_key: str
    data_dir: Path = Path("/data")
    public_url: str | None = None
    signing_secret: str | None = None
    file_ttl_hours: float = 6.0
    link_ttl_seconds: int = 3600
    max_concurrent_jobs: int = 2
    max_pending_jobs: int = 20
    job_timeout_seconds: int = 600
    max_wait_seconds: int = 25
    max_file_mb: int = 1024
    max_items: int = 30
    platform_min_interval: float = 2.0
    proxies: Mapping[str, str] = field(default_factory=dict)
    worker_cmd: tuple[str, ...] = (sys.executable, "-m", "app.worker")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        def get(name: str, default=None):
            return env.get(ENV_PREFIX + name, "").strip() or default

        # MEDIAHOOK_PROXY 是共用預設值，MEDIAHOOK_PROXY_<平台> 可個別覆寫
        default_proxy = get("PROXY")
        proxies = {}
        for platform in PLATFORMS:
            if proxy := get(f"PROXY_{platform.upper()}", default_proxy):
                proxies[platform] = proxy

        return cls(
            api_key=get("API_KEY", ""),
            data_dir=Path(get("DATA_DIR", "/data")),
            public_url=get("PUBLIC_URL", "").rstrip("/") or None,
            signing_secret=get("SIGNING_SECRET"),
            file_ttl_hours=float(get("FILE_TTL_HOURS", 6)),
            link_ttl_seconds=int(get("LINK_TTL_SECONDS", 3600)),
            max_concurrent_jobs=int(get("MAX_CONCURRENT_JOBS", 2)),
            max_pending_jobs=int(get("MAX_PENDING_JOBS", 20)),
            job_timeout_seconds=int(get("JOB_TIMEOUT_SECONDS", 600)),
            max_wait_seconds=int(get("MAX_WAIT_SECONDS", 25)),
            max_file_mb=int(get("MAX_FILE_MB", 1024)),
            max_items=int(get("MAX_ITEMS", 30)),
            platform_min_interval=float(get("PLATFORM_MIN_INTERVAL", 2)),
            proxies=proxies,
        )

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def cookies_dir(self) -> Path:
        return self.data_dir / "cookies"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mediahook.db"

    @property
    def signing_key(self) -> bytes:
        if self.signing_secret:
            return self.signing_secret.encode()
        return hashlib.sha256(b"mediahook-signing:" + self.api_key.encode()).digest()
