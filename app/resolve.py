"""展開短網址（vm.tiktok.com、xhslink.com、t.co…）。

每一跳都重新檢查白名單，避免短網址把我們導去打內網或任意網站。
"""

from __future__ import annotations

import logging
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path
from urllib.parse import urljoin

import requests

from .platforms import check_url, needs_expansion

log = logging.getLogger("mediahook.resolve")

MAX_HOPS = 5
TIMEOUT_SECONDS = 15
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def expand_url(url: str, proxy: str | None = None, cookies: Path | None = None) -> str:
    if not needs_expansion(url):
        return url
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}
        if cookies:
            session.cookies.update(_load_cookies(cookies))

        for _ in range(MAX_HOPS):
            if not needs_expansion(url):
                break
            try:
                response = session.get(url, allow_redirects=False, timeout=TIMEOUT_SECONDS, stream=True)
                response.close()
            except requests.RequestException as exc:
                log.warning("短網址展開失敗 %s：%s", url, exc)
                break
            location = response.headers.get("Location")
            if not (response.is_redirect and location):
                break
            url, _platform = check_url(urljoin(url, location))
    return url


def _load_cookies(path: Path) -> MozillaCookieJar:
    jar = MozillaCookieJar()
    try:
        jar.load(str(path), ignore_discard=True, ignore_expires=True)
    except (OSError, LoadError) as exc:
        log.warning("cookies 讀取失敗 %s：%s", path.name, exc)
    return jar
