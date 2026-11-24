"""Download feeds politely (conditional requests, timeouts, a few at a time)."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import feedparser
import httpx

from .config import Feed

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    feed: Feed
    status: str  # "ok", "not_modified", or "error"
    entries: list = field(default_factory=list)
    etag: str = ""
    modified: str = ""
    error: str = ""


def _parse(feed: Feed, content: bytes) -> FetchResult:
    parsed = feedparser.parse(content)
    if not parsed.entries:
        problem = getattr(parsed, "bozo_exception", None)
        if problem or not parsed.get("feed"):
            return FetchResult(feed, "error", error=f"Not a readable feed ({problem or 'no entries'})")
    return FetchResult(feed, "ok", entries=list(parsed.entries))


TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _attempt(feed: Feed, client: httpx.Client, etag: str, modified: str) -> tuple[FetchResult, bool]:
    """One request. Returns (result, worth_retrying)."""
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    try:
        resp = client.get(feed.url, headers=headers)
    except httpx.TimeoutException:
        return FetchResult(feed, "error", error="Timed out"), True
    except httpx.HTTPError as exc:
        return FetchResult(feed, "error", error=f"{type(exc).__name__}: {exc}"[:300]), True

    if resp.status_code == 304:
        return FetchResult(feed, "not_modified", etag=etag, modified=modified), False
    if resp.status_code in (401, 403):
        return FetchResult(feed, "error", error=f"HTTP {resp.status_code}: the site refuses automated "
                                                 "requests from GitHub's servers"), False
    if resp.status_code >= 400:
        return FetchResult(feed, "error", error=f"HTTP {resp.status_code}"), resp.status_code in TRANSIENT_STATUS

    result = _parse(feed, resp.content)
    if result.status == "ok":
        result.etag = resp.headers.get("ETag", "")
        result.modified = resp.headers.get("Last-Modified", "")
    return result, False


def fetch_one(feed: Feed, client: httpx.Client, etag: str = "", modified: str = "",
              retry_pause: float = 3.0) -> FetchResult:
    """Fetch one feed, retrying once on temporary trouble (timeouts, rate limits,
    server errors). A site that deliberately blocks us is reported, not retried."""
    if feed.url.startswith("file://"):
        path = Path(url2pathname(urlparse(feed.url).path))
        try:
            return _parse(feed, path.read_bytes())
        except OSError as exc:
            return FetchResult(feed, "error", error=str(exc))

    result, retry = _attempt(feed, client, etag, modified)
    if retry:
        time.sleep(retry_pause)
        second, _ = _attempt(feed, client, etag, modified)
        if second.status == "error":
            second.error = f"{result.error}, then again on retry: {second.error}"
        return second
    return result


def fetch_all(feeds: list[Feed], meta: dict[str, dict], *, timeout: float, workers: int,
              user_agent: str) -> list[FetchResult]:
    """Fetch every feed. `meta` maps feed id -> {"etag":..., "modified":...} from the last run."""
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    }
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        def run(feed: Feed) -> FetchResult:
            m = meta.get(feed.id, {})
            res = fetch_one(feed, client, m.get("etag", ""), m.get("modified", ""))
            log.info("%-45s %s%s", feed.name[:45], res.status,
                     f" ({len(res.entries)} entries)" if res.status == "ok" else
                     f" - {res.error}" if res.error else "")
            return res

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(run, feeds))
