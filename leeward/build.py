"""One run: fetch, label new items, apply the dial, write everything out."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import pages, render
from .config import Settings
from .fetch import FetchResult, fetch_all
from .items import Item, iso, normalize_entry, parse_iso
from .rules import Decision, decide, display_title
from .state import State

log = logging.getLogger(__name__)


@dataclass
class BuildReport:
    fetched_ok: int = 0
    fetched_errors: int = 0
    new_items: int = 0
    labeled: int = 0
    waiting: int = 0
    shown: dict[int, int] = field(default_factory=dict)
    levels: dict[int, dict] = field(default_factory=dict)
    capped: bool = False
    tagger_errors: int = 0


def _feed_url(settings: Settings, path: str) -> str:
    return f"{settings.base_url}/{path}" if settings.base_url else path


def ingest(results: list[FetchResult], state: State, settings: Settings, now: datetime,
           report: BuildReport) -> None:
    day = now.date().isoformat()
    oldest = now - timedelta(days=settings.max_age_days)
    # Direct sources first, so when a story shows up in Google News too, the direct copy wins.
    for res in sorted(results, key=lambda r: r.feed.is_google_news):
        src = state.source(res.feed.id)
        src["name"] = res.feed.name
        src["category"] = res.feed.category
        if res.status == "error":
            report.fetched_errors += 1
            src["consecutive_failures"] = src.get("consecutive_failures", 0) + 1
            src["last_error"] = res.error
            continue
        report.fetched_ok += 1
        src["consecutive_failures"] = 0
        src["last_error"] = ""
        src["last_ok"] = iso(now)
        if res.status == "not_modified":
            continue
        src["etag"], src["modified"] = res.etag, res.modified

        candidates, too_old, newest = [], 0, None
        for entry in res.entries:
            item = normalize_entry(entry, res.feed, now)
            if not item:
                continue
            newest = max(newest, item.published_dt) if newest else item.published_dt
            if item.published_dt >= oldest:
                candidates.append(item)
            else:
                too_old += 1
        candidates.sort(key=lambda i: i.published, reverse=True)
        added, duplicates, dup_sources = 0, 0, set()
        for item in candidates[: res.feed.max_items]:
            outcome, other = state.add_item(item)
            if outcome == "added":
                added += 1
            elif outcome == "duplicate":
                duplicates += 1
                dup_sources.add(other)
        # What this feed looked like on its latest successful fetch; the health page
        # uses it to explain a feed that's reachable but contributes nothing.
        src["last_fetch"] = {
            "entries": len(res.entries),
            "added": added,
            "too_old": too_old,
            "duplicates": duplicates,
            "duplicate_of": sorted(dup_sources)[:3],
            "newest": iso(newest) if newest else "",
        }
        if added:
            state.bump(res.feed.id, day, "new", added)
            src["last_new_item"] = iso(now)
            report.new_items += added


def label(state: State, tagger, settings: Settings, now: datetime, report: BuildReport,
          limit: int | None = None) -> list[str]:
    """Label items that don't have labels yet. Returns ids labeled this run."""
    waiting = [i for i in state.items.values() if i.id not in state.tags]
    waiting.sort(key=lambda i: i.published, reverse=True)
    month = now.strftime("%Y-%m")
    usage = state.month_usage(month)
    room = settings.monthly_item_cap - usage["items"]
    if room <= 0:
        report.capped = True
        report.waiting = len(waiting)
        log.warning("Monthly label cap (%d) reached; new items stay unlabeled until next month "
                    "or until you raise model.monthly_item_cap.", settings.monthly_item_cap)
        return []
    todo = waiting[: min(room, limit) if limit is not None else room]
    if not todo:
        return []
    new_tags = tagger.tag(todo)
    state.tags.update(new_tags)
    u = tagger.usage
    usage["items"] += len(new_tags)
    usage["input_tokens"] += u.input_tokens
    usage["output_tokens"] += u.output_tokens
    usage["calls"] += u.calls
    usage["errors"] += u.errors
    report.labeled = len(new_tags)
    report.tagger_errors = u.errors
    report.waiting = len(waiting) - len(new_tags)
    return list(new_tags)


def build(settings: Settings, tagger, *, state_dir: str | Path, out_dir: str | Path,
          now: datetime | None = None, fetch_results: list[FetchResult] | None = None,
          label_limit: int | None = None) -> BuildReport:
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    report = BuildReport()
    state = State(state_dir)
    out = Path(out_dir)
    (out / "feeds").mkdir(parents=True, exist_ok=True)

    feeds = [f for f in settings.feeds if f.enabled]
    if fetch_results is None:
        fetch_results = fetch_all(
            feeds, {f.id: state.source(f.id) for f in feeds},
            timeout=settings.timeout, workers=settings.workers, user_agent=settings.user_agent)
    ingest(fetch_results, state, settings, now, report)
    newly_labeled = label(state, tagger, settings, now, report, label_limit)

    # Health stats: count each item once, at the default level, when it first gets a label.
    day = now.date().isoformat()
    default = settings.levels[settings.default_level]
    for item_id in newly_labeled:
        item = state.items[item_id]
        d = decide(item, state.tags.get(item_id), default, settings)
        state.bump(item.source_id, day, "kept" if d.visible else "hidden")

    # Decide everything in the output window at every dial level.
    window_start = now - timedelta(days=settings.keep_days)
    active_feeds = {f.id for f in feeds}
    window = [i for i in state.items.values()
              if parse_iso(i.first_seen) >= window_start and i.source_id in active_feeds
              and i.category in settings.categories]
    window.sort(key=lambda i: i.published, reverse=True)

    decisions: dict[int, dict[str, Decision]] = {
        lvl: {i.id: decide(i, state.tags.get(i.id), level, settings) for i in window}
        for lvl, level in settings.levels.items()
    }

    def entries_for(level: int, category: str | None = None) -> list[tuple[Item, dict | None, str]]:
        """Visible items, newest first, with each section capped so no one section
        crowds out the rest. Significant stories (see dial.always_include_significance)
        never get cut by a cap; otherwise a burst of routine items could push out the
        one story the dial was meant to let through."""
        per_cat: dict[str, int] = {}
        chosen = []
        for item in window:
            if category and item.category != category:
                continue
            if not decisions[level][item.id].visible:
                continue
            tags = state.tags.get(item.id)
            significant = bool(tags) and tags.get("significance", 0) >= settings.cap_exempt_significance
            if not significant:
                cap = settings.categories[item.category].max_items
                if per_cat.get(item.category, 0) >= cap:
                    continue
                per_cat[item.category] = per_cat.get(item.category, 0) + 1
            chosen.append((item, tags, display_title(item, tags, settings)))
        return chosen

    def summarize(entries) -> dict:
        by_cat: dict[str, int] = {}
        negative = 0
        for item, tags, _ in entries:
            by_cat[item.category] = by_cat.get(item.category, 0) + 1
            # Count only sections the dial controls; your teams' bad news shows at every level by design.
            if tags and tags.get("tone") == "negative" and settings.categories[item.category].mode == "dial":
                negative += 1
        return {"total": len(entries), "negative": negative, "by_category": by_cat}

    outlines = []
    for lvl, level in settings.levels.items():
        entries = entries_for(lvl)
        report.shown[lvl] = len(entries)
        report.levels[lvl] = summarize(entries)
        path = f"feeds/digest-{lvl}.xml"
        title = f"{settings.title} · {lvl} {level.name}"
        (out / path).write_text(render.rss(
            title, f"Everything at dial level {lvl} ({level.name}).", settings.base_url,
            _feed_url(settings, path), entries, now, feed_key=f"d{lvl}"), encoding="utf-8")
        # Each level gets its own folder, so picking today's level means picking a folder.
        outlines.append((title, title, _feed_url(settings, path)))

    for key, cat in settings.categories.items():
        entries = entries_for(settings.default_level, key)
        path = f"feeds/{key}.xml"
        title = f"{settings.title} · {cat.label}"
        (out / path).write_text(render.rss(
            title, f"{cat.label}, filtered at dial level {settings.default_level} ({default.name}).",
            settings.base_url, _feed_url(settings, path), entries, now, feed_key=key), encoding="utf-8")
        outlines.append((f"{settings.title} sections", title, _feed_url(settings, path)))

    (out / "subscriptions.opml").write_text(render.opml(f"{settings.title} feeds", outlines), encoding="utf-8")

    hidden = [(i, decisions[settings.default_level][i.id]) for i in window
              if not decisions[settings.default_level][i.id].visible]
    month_usage = state.month_usage(now.strftime("%Y-%m"))
    (out / "index.html").write_text(pages.index_page(settings, outlines, report, month_usage, now), encoding="utf-8")
    (out / "hidden.html").write_text(pages.hidden_page(settings, hidden, state.tags, now), encoding="utf-8")
    (out / "health.html").write_text(pages.health_page(settings, state, feeds, month_usage, now), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    state.prune(now, settings.keep_days, settings.max_age_days)
    state.save()
    return report
