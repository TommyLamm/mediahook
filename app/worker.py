"""下載 worker：由 JobManager 以子程序啟動，一次處理一個任務。

用法：python -m app.worker <job_dir>
讀 <job_dir>/request.json，寫 <job_dir>/result.json，成品放在 <job_dir>/files/。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path

from .errors import MediaHookError
from .extractors import ExtractContext, run_chain
from .normalize import normalize
from .platforms import check_url
from .resolve import expand_url

log = logging.getLogger("mediahook.worker")


def process(request: dict, job_dir: Path) -> dict:
    proxies = request.get("proxies") or {}
    cookies_dir = Path(request["cookies_dir"])

    url, platform = check_url(request["url"])
    url = expand_url(url, proxy=proxies.get(platform), cookies=_cookie_file(cookies_dir, platform))
    url, platform = check_url(url)  # 短網址展開後平台可能不同（例如 t.co）
    log.info("網址：%s（%s）", url, platform)

    raw_dir = job_dir / "raw"
    cookies = _copy_cookies(cookies_dir, platform, job_dir)
    try:
        ctx = ExtractContext(
            url=url,
            platform=platform,
            mode=request["mode"],
            raw_dir=raw_dir,
            cookies=cookies,
            proxy=proxies.get(platform),
            max_bytes=int(request["max_file_mb"]) * 1024 * 1024,
            max_items=int(request["max_items"]),
        )
        extracted = run_chain(ctx)
        stem = _safe_stem(f"{platform}_{extracted.post_id or job_dir.name}")
        outputs = normalize(extracted.files, request["mode"], job_dir / "files", stem)
    finally:
        if cookies:
            cookies.unlink(missing_ok=True)
        shutil.rmtree(raw_dir, ignore_errors=True)

    return {
        "url": url,
        "platform": platform,
        "caption": extracted.caption,
        "author": extracted.author,
        "title": extracted.title,
        "post_id": extracted.post_id,
        "files": [{"name": o.path.name, "kind": o.kind, "size": o.path.stat().st_size} for o in outputs],
    }


def _cookie_file(cookies_dir: Path, platform: str) -> Path | None:
    path = cookies_dir / f"{platform}.txt"
    return path if path.is_file() else None


def _copy_cookies(cookies_dir: Path, platform: str, job_dir: Path) -> Path | None:
    """複製一份給這個任務用：yt-dlp 會把 cookies 寫回檔案，不能讓它動到原檔。"""
    source = _cookie_file(cookies_dir, platform)
    if source is None:
        return None
    target = job_dir / "cookies.txt"
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)
    return target


def _safe_stem(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", text)[:60] or "media"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    job_dir = Path(argv[1])
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        request = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
        result = {"ok": True, **process(request, job_dir)}
    except MediaHookError as exc:
        log.warning("%s: %s", exc.code, exc.message)
        result = {"ok": False, "code": exc.code, "message": exc.message}
    except Exception as exc:
        log.exception("worker 發生未預期錯誤")
        error = MediaHookError("EXTRACT_FAILED", detail=f"{type(exc).__name__}: {exc}"[:200])
        result = {"ok": False, "code": error.code, "message": error.message}

    tmp = job_dir / "result.json.tmp"
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, job_dir / "result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
