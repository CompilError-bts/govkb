from __future__ import annotations

import time
import re
import urllib.error
import urllib.request
from urllib.parse import urljoin
from dataclasses import dataclass
from typing import Iterable


DEFAULT_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
)


@dataclass
class FetchResult:
    url: str
    text: str
    encoding: str
    status: int


def fetch_text(
    url: str,
    *,
    timeout: int = 15,
    retries: int = 2,
    user_agents: Iterable[str] = DEFAULT_USER_AGENTS,
    follow_meta_refresh: bool = True,
) -> FetchResult:
    """Fetch a URL as text with simple retry and charset detection."""

    agents = tuple(user_agents) or DEFAULT_USER_AGENTS
    last_error: Exception | None = None

    current_url = url
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            current_url,
            headers={
                "User-Agent": agents[attempt % len(agents)],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read()
                encoding = response.headers.get_content_charset() or _guess_encoding(body)
                text = body.decode(encoding, errors="replace")
                refresh_url = _find_meta_refresh_url(text, response.geturl())
                if follow_meta_refresh and refresh_url and refresh_url != response.geturl():
                    return fetch_text(
                        refresh_url,
                        timeout=timeout,
                        retries=retries,
                        user_agents=agents,
                        follow_meta_refresh=follow_meta_refresh,
                    )
                return FetchResult(
                    url=response.geturl(),
                    text=text,
                    encoding=encoding,
                    status=getattr(response, "status", 200),
                )
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))

    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def _guess_encoding(body: bytes) -> str:
    head = body[:2048].decode("ascii", errors="ignore").lower()
    if "charset=gb2312" in head or "charset=gbk" in head:
        return "gb18030"
    if "charset=utf-8" in head or "charset=\"utf-8\"" in head:
        return "utf-8"
    return "utf-8"


def _find_meta_refresh_url(html: str, base_url: str) -> str:
    match = re.search(
        r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\']*url=([^"\']+)["\']',
        html,
        re.I,
    )
    if not match:
        return ""
    return urljoin(base_url, match.group(1).strip())
