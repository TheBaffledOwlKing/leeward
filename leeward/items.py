"""Turn raw feed entries into clean, comparable items."""

from __future__ import annotations

import calendar
import hashlib
import html
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser

from .config import Feed

SUMMARY_CHARS = 400
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif")


@dataclass
class Item:
    id: str
    source_id: str
    source_name: str
    category: str
    title: str
    link: str
    summary: str
    image: str
    published: str   # ISO 8601, UTC
    first_seen: str  # ISO 8601, UTC
    publisher: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        return cls(**{k: d.get(k, "") for k in cls.__dataclass_fields__})

    @property
    def published_dt(self) -> datetime:
        return parse_iso(self.published)


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class _TextAndImage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.image = ""
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "img" and not self.image:
            src = dict(attrs).get("src") or ""
            if src.startswith("http"):
                self.image = src
        elif tag in ("p", "br", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_html(raw: str) -> tuple[str, str]:
    """Return (plain text, first image URL) from an HTML fragment."""
    if not raw:
        return "", ""
    parser = _TextAndImage()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # malformed HTML; fall back to a crude strip
        text = re.sub(r"<[^>]+>", " ", raw)
        return re.sub(r"\s+", " ", html.unescape(text)).strip(), ""
    text = re.sub(r"\s+", " ", "".join(parser.parts)).strip()
    return text, parser.image


def title_key(title: str) -> str:
    """Normalized title used to spot the same story from different feeds."""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()[:90]


def make_id(feed: Feed, guid: str) -> str:
    return hashlib.sha1(f"{feed.id}|{guid}".encode()).hexdigest()[:16]


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
            except (OverflowError, ValueError, TypeError):
                continue
    return None


def _entry_image(entry, *html_bits: str) -> str:
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key) or []:
            url = media.get("url", "")
            medium = media.get("medium", "")
            mtype = media.get("type", "")
            if url.startswith("http") and (medium == "image" or mtype.startswith("image")
                                           or url.lower().split("?")[0].endswith(IMAGE_EXTENSIONS)
                                           or key == "media_thumbnail"):
                return url
    for enc in entry.get("enclosures") or []:
        url = enc.get("href") or enc.get("url") or ""
        if url.startswith("http") and (enc.get("type", "").startswith("image")
                                       or url.lower().split("?")[0].endswith(IMAGE_EXTENSIONS)):
            return url
    for bit in html_bits:
        _, img = strip_html(bit)
        if img:
            return img
    return ""


def normalize_entry(entry, feed: Feed, now: datetime) -> Item | None:
    raw_title = entry.get("title") or ""
    title, _ = strip_html(html.unescape(raw_title)) if "<" in raw_title else (html.unescape(raw_title).strip(), "")
    link = entry.get("link") or ""
    if not title or not link:
        return None

    publisher = ""
    source = entry.get("source")
    if isinstance(source, dict):
        publisher = source.get("title", "") or ""
    if feed.is_google_news:
        # Google News titles end with " - Publisher"; drop it.
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].rstrip()
        elif " - " in title:
            head, _, tail = title.rpartition(" - ")
            if head and len(tail) <= 40:
                title, publisher = head, publisher or tail

    content_html = ""
    if entry.get("content"):
        content_html = entry["content"][0].get("value", "") or ""
    summary_html = entry.get("summary") or entry.get("description") or ""
    summary, _ = strip_html(summary_html)
    if not summary and content_html:
        summary, _ = strip_html(content_html)
    if feed.is_google_news:
        # Google's summary just repeats the headline and publisher.
        summary = ""
    if summary and title_key(summary).startswith(title_key(title)):
        summary = summary[len(title):].strip(" -–:|")
    if len(summary) > SUMMARY_CHARS:
        summary = summary[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"

    image = _entry_image(entry, content_html, summary_html)
    published = _entry_time(entry) or now
    if published > now:
        published = now
    # Without a guid, the link alone isn't enough: APOD's newest item always links to
    # the same "today" page, so each day's picture would look like yesterday's.
    guid = entry.get("id") or entry.get("guid") or f"{link}|{title}"

    return Item(
        id=make_id(feed, guid),
        source_id=feed.id,
        source_name=feed.name,
        category=feed.category,
        title=title,
        link=link,
        summary=summary,
        image=image,
        published=iso(published),
        first_seen=iso(now),
        publisher=publisher,
    )
