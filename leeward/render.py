"""Write the output feeds and pages."""

from __future__ import annotations

from datetime import datetime
from email.utils import format_datetime
from html import escape
from xml.sax.saxutils import escape as xml_escape, quoteattr

from .items import Item

MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif"}


def _image_type(url: str) -> str:
    path = url.lower().split("?")[0]
    for ext, mime in MIME_BY_EXT.items():
        if path.endswith(ext):
            return mime
    return "image/jpeg"


def _clean_xml_text(text: str) -> str:
    # Drop control characters XML 1.0 can't carry.
    return "".join(ch for ch in text if ch in "\t\n\r" or ord(ch) >= 0x20)


def _x(text: str) -> str:
    return xml_escape(_clean_xml_text(text or ""))


def item_body_html(item: Item, tags: dict | None, title_shown: str) -> str:
    parts = []
    if item.image:
        parts.append(f'<p><img src="{escape(item.image)}" alt="" style="max-width:100%"></p>')
    if item.summary:
        parts.append(f"<p>{escape(item.summary)}</p>")
    meta = [escape(item.publisher or item.source_name)]
    if tags:
        meta.append(escape(", ".join(t.replace("_", " ") for t in tags.get("topics", []))))
    parts.append(f'<p style="color:#777;font-size:0.9em">{" · ".join(m for m in meta if m)}</p>')
    if title_shown != item.title:
        parts.append(f'<p style="color:#777;font-size:0.9em">Original headline: {escape(item.title)}</p>')
    return "".join(parts)


def rss(title: str, description: str, link: str, self_url: str, entries: list[tuple[Item, dict | None, str]],
        now: datetime) -> str:
    """entries: (item, tags, title to show)."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/" '
           'xmlns:atom="http://www.w3.org/2005/Atom">',
           "<channel>",
           f"<title>{_x(title)}</title>",
           f"<link>{_x(link or self_url or 'https://example.invalid/')}</link>",
           f"<description>{_x(description)}</description>",
           f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>",
           "<generator>Leeward</generator>"]
    if self_url:
        out.append(f'<atom:link href={quoteattr(self_url)} rel="self" type="application/rss+xml"/>')
    for item, tags, shown in entries:
        out.append("<item>")
        out.append(f"<title>{_x(shown)}</title>")
        out.append(f"<link>{_x(item.link)}</link>")
        out.append(f'<guid isPermaLink="false">{_x(item.id)}</guid>')
        out.append(f"<pubDate>{format_datetime(item.published_dt)}</pubDate>")
        out.append(f"<category>{_x(item.category)}</category>")
        out.append(f"<description>{_x(item_body_html(item, tags, shown))}</description>")
        if item.image:
            out.append(f'<media:content url={quoteattr(_clean_xml_text(item.image))} medium="image"/>')
            out.append(f'<enclosure url={quoteattr(_clean_xml_text(item.image))} '
                       f'type="{_image_type(item.image)}" length="0"/>')
        out.append("</item>")
    out += ["</channel>", "</rss>", ""]
    return "\n".join(out)


def opml(title: str, outlines: list[tuple[str, str, str]]) -> str:
    """outlines: (folder, feed title, absolute feed URL)."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<opml version="2.0">',
           f"<head><title>{_x(title)}</title></head>", "<body>"]
    folders: dict[str, list[tuple[str, str]]] = {}
    for folder, name, url in outlines:
        folders.setdefault(folder, []).append((name, url))
    for folder, feeds in folders.items():
        out.append(f"<outline text={quoteattr(folder)} title={quoteattr(folder)}>")
        for name, url in feeds:
            out.append(f"<outline type=\"rss\" text={quoteattr(name)} title={quoteattr(name)} "
                       f"xmlUrl={quoteattr(url)}/>")
        out.append("</outline>")
    out += ["</body>", "</opml>", ""]
    return "\n".join(out)


PAGE_CSS = """
:root{color-scheme:light dark;--fg:#1d1d1f;--muted:#6b6b70;--bg:#fbfbf8;--card:#fff;--line:#e4e4df;--accent:#2f6f5e}
@media (prefers-color-scheme:dark){:root{--fg:#ececea;--muted:#a0a09c;--bg:#171817;--card:#1f201f;--line:#333431;--accent:#7cc4ad}}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:var(--fg);background:var(--bg)}
main{max-width:860px;margin:0 auto;padding:28px 18px 60px}h1{font-size:1.7rem;margin:0 0 4px}h2{font-size:1.15rem;margin:28px 0 10px}
.muted{color:var(--muted)}a{color:var(--accent)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.card b{display:block}
code{font-size:.85em;word-break:break-all}table{width:100%;border-collapse:collapse;font-size:.92rem}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}
.ok{color:#2e8b57}.warn{color:#c27c0e}.bad{color:#c0392b}details{border-bottom:1px solid var(--line);padding:6px 0}
summary{cursor:pointer;color:var(--muted)}details p{margin:6px 0 4px}
"""


def page(title: str, body: str) -> str:
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex">'
            f"<title>{escape(title)}</title><style>{PAGE_CSS}</style></head>"
            f"<body><main>{body}</main></body></html>")
