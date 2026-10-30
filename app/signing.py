"""下載連結的 HMAC 簽名：捷徑或 Safari 不用帶金鑰也能抓檔案，但連結會過期。"""

from __future__ import annotations

import hashlib
import hmac
import time


def sign(key: bytes, job_id: str, filename: str, expires: int) -> str:
    message = f"{job_id}/{filename}/{expires}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:32]


def verify(key: bytes, job_id: str, filename: str, expires: int, signature: str,
           now: float | None = None) -> bool:
    if expires < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(sign(key, job_id, filename, expires), signature)
