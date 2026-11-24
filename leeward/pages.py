"""The three small web pages: index, hidden drawer, and source health."""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from .config import Feed, Settings
from .items import Item, parse_iso
from .render import page
from .rules import Decision

HEALTH_WINDOW_DAYS = 14


def _local(settings: Settings, dt: datetime) -> str:
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    return dt.astimezone(tz).strftime("%a %b %-d, %-I:%M %p %Z")


def _cost(settings: Settings, usage: dict) -> float:
    return (usage.get("input_tokens", 0) * settings.input_price
            + usage.get("output_tokens", 0) * settings.output_price) / 1_000_000


def index_page(settings: Settings, outlines, report, usage: dict, now: datetime) -> str:
    level_cards = []
    for lvl, level in settings.levels.items():
        path = f"feeds/digest-{lvl}.xml"
        url = f"{settings.base_url}/{path}" if settings.base_url else path
        level_cards.append(
            f'<div class="card"><b>{lvl} · {escape(level.name)}</b>'
            f'<span class="muted">{report.shown.get(lvl, 0)} items right now</span><br>'
            f'<a href="{escape(path)}">feed</a> · <code>{escape(url)}</code></div>')
    section_cards = []
    for key, cat in settings.categories.items():
        path = f"feeds/{key}.xml"
        section_cards.append(f'<div class="card"><b>{escape(cat.label)}</b>'
                             f'<a href="{escape(path)}">feed</a></div>')
    body = f"""
<h1>{escape(settings.title)}</h1>
<p class="muted">Updated {_local(settings, now)}. {report.new_items} new items this run,
{report.labeled} labeled{', ' + str(report.waiting) + ' waiting' if report.waiting else ''}.</p>

<h2>Today's dial</h2>
<p>Subscribe to the levels you want, each in its own folder in your reader, and open whichever fits the day.
Uplifting and neutral items appear at every level; the dial only decides which negative stories get through.</p>
<div class="grid">{''.join(level_cards)}</div>

<h2>Sections</h2>
<p class="muted">Each section on its own, at the default level ({settings.default_level} · {escape(settings.levels[settings.default_level].name)}).</p>
<div class="grid">{''.join(section_cards)}</div>

<h2>Set up your reader</h2>
<p><a href="subscriptions.opml">Download subscriptions.opml</a> and import it in FeedFlow (Settings, Import feeds),
then delete the levels or sections you don't want.</p>

<h2>Behind the curtain</h2>
<p><a href="hidden.html">Hidden drawer</a>: what got filtered at the default level and why.<br>
<a href="health.html">Source health</a>: broken, quiet, or mostly-hidden feeds, plus this month's API cost
(about ${_cost(settings, usage):.2f} so far).</p>
"""
    return page(settings.title, body)


def hidden_page(settings: Settings, hidden: list[tuple[Item, Decision]], tags: dict, now: datetime) -> str:
    level = settings.levels[settings.default_level]
    groups: dict[str, list[tuple[Item, Decision]]] = {}
    for item, decision in hidden:
        groups.setdefault(settings.categories[item.category].label, []).append((item, decision))
    sections = []
    for label, rows in groups.items():
        lines = []
        for item, decision in rows:
            sig = tags.get(item.id, {}).get("significance")
            sig_txt = f" · significance {sig}" if sig is not None else ""
            lines.append(
                f"<details><summary>{escape(decision.reason)} · {escape(item.publisher or item.source_name)}"
                f"{sig_txt}</summary><p><a href=\"{escape(item.link)}\" rel=\"noreferrer\">{escape(item.title)}</a></p>"
                f"</details>")
        sections.append(f"<h2>{escape(label)} ({len(rows)})</h2>{''.join(lines)}")
    body = f"""
<h1>Hidden drawer</h1>
<p class="muted">Filtered at level {settings.default_level} ({escape(level.name)}) over the last
{settings.keep_days:g} days. Headlines stay folded until you open one. Updated {_local(settings, now)}.</p>
<p><a href="index.html">Back</a></p>
{''.join(sections) or '<p>Nothing hidden right now.</p>'}
"""
    return page(f"{settings.title} · hidden", body)


def source_status(src: dict, now: datetime) -> tuple[str, str]:
    """(css class, message) describing a source's health."""
    fails = src.get("consecutive_failures", 0)
    if fails >= 3 or (fails and not src.get("last_ok")):
        return "bad", f"Failing ({fails} run{'' if fails == 1 else 's'} in a row): {src.get('last_error', '')}"
    cutoff = (now - timedelta(days=HEALTH_WINDOW_DAYS)).date().isoformat()
    recent = [v for d, v in src.get("days", {}).items() if d >= cutoff]
    kept = sum(v.get("kept", 0) for v in recent)
    hidden = sum(v.get("hidden", 0) for v in recent)
    last_new = src.get("last_new_item")
    if last_new and parse_iso(last_new) < now - timedelta(days=21):
        return "warn", "Quiet: nothing new in 3 weeks. It may be dead."
    if kept + hidden >= 10 and hidden / (kept + hidden) >= 0.7:
        return "warn", f"Mostly hidden ({hidden} of {kept + hidden}). Consider removing it."
    if fails:
        return "warn", f"Failed last run: {src.get('last_error', '')}"
    if not last_new and src.get("last_ok"):
        return "warn", "Reachable, but nothing new yet."
    return "ok", "OK"


def health_page(settings: Settings, state, feeds: list[Feed], usage: dict, now: datetime) -> str:
    cutoff = (now - timedelta(days=HEALTH_WINDOW_DAYS)).date().isoformat()
    rows = []
    order = {"bad": 0, "warn": 1, "ok": 2}
    table = []
    for feed in feeds:
        src = state.sources.get(feed.id, {})
        css, msg = source_status(src, now)
        recent = [v for d, v in src.get("days", {}).items() if d >= cutoff]
        new = sum(v.get("new", 0) for v in recent)
        kept = sum(v.get("kept", 0) for v in recent)
        hidden = sum(v.get("hidden", 0) for v in recent)
        table.append((order[css], feed.category, feed.name, css, msg, new, kept, hidden))
    for _, category, name, css, msg, new, kept, hidden in sorted(table):
        rows.append(f"<tr><td>{escape(name)}<br><span class='muted'>{escape(category)}</span></td>"
                    f"<td class='{css}'>{escape(msg)}</td><td>{new}</td><td>{kept}</td><td>{hidden}</td></tr>")
    body = f"""
<h1>Source health</h1>
<p class="muted">Last {HEALTH_WINDOW_DAYS} days, counted at level {settings.default_level}. Updated {_local(settings, now)}.</p>
<p><a href="index.html">Back</a></p>
<p>This month: {usage.get('items', 0)} items labeled in {usage.get('calls', 0)} calls,
{usage.get('input_tokens', 0):,} input and {usage.get('output_tokens', 0):,} output tokens,
about <b>${_cost(settings, usage):.2f}</b>{f", {usage.get('errors')} failed calls" if usage.get('errors') else ''}.</p>
<table><tr><th>Source</th><th>Status</th><th>New</th><th>Kept</th><th>Hidden</th></tr>{''.join(rows)}</table>
"""
    return page(f"{settings.title} · health", body)
