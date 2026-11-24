"""Small JSON files that carry memory between runs."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .items import Item, iso, parse_iso, title_key

STATS_DAYS = 30


def _load(path: Path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        # A corrupted state file shouldn't take the whole build down; start that piece fresh.
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


class State:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.items: dict[str, Item] = {
            k: Item.from_dict(v) for k, v in _load(self.dir / "items.json", {}).items()
        }
        self.tags: dict[str, dict] = _load(self.dir / "tags.json", {})
        self.sources: dict[str, dict] = _load(self.dir / "sources.json", {})
        self.usage: dict[str, dict] = _load(self.dir / "usage.json", {})
        self._title_index = {title_key(i.title): i.id for i in self.items.values()}

    # ---------------------------------------------------------------- items
    def has_title(self, title: str) -> bool:
        return title_key(title) in self._title_index

    def add_item(self, item: Item) -> tuple[str, str]:
        """Add a new item. Returns (outcome, detail):
        ("added", ""), ("known", "") if this feed already gave us this item, or
        ("duplicate", <other source name>) if another feed already has the same story."""
        if item.id in self.items:
            return "known", ""
        key = title_key(item.title)
        existing = self._title_index.get(key)
        if existing is not None and existing in self.items:
            other = self.items[existing]
            if other.source_id == item.source_id:
                return "known", ""
            return "duplicate", other.source_name
        self.items[item.id] = item
        self._title_index[key] = item.id
        return "added", ""

    # ---------------------------------------------------------------- sources
    def source(self, source_id: str) -> dict:
        return self.sources.setdefault(source_id, {
            "etag": "", "modified": "", "last_ok": "", "last_error": "",
            "consecutive_failures": 0, "last_new_item": "", "days": {},
        })

    def bump(self, source_id: str, day: str, field: str, n: int = 1) -> None:
        days = self.source(source_id)["days"]
        bucket = days.setdefault(day, {"new": 0, "kept": 0, "hidden": 0})
        bucket[field] = bucket.get(field, 0) + n

    # ---------------------------------------------------------------- usage
    def month_usage(self, month: str) -> dict:
        return self.usage.setdefault(month, {"items": 0, "input_tokens": 0, "output_tokens": 0,
                                             "calls": 0, "errors": 0})

    # ---------------------------------------------------------------- housekeeping
    def prune(self, now: datetime, keep_days: float, max_age_days: float) -> None:
        # Keep items a little past the output window so a story that lingers in a
        # source feed isn't re-added and re-labeled as "new".
        horizon = now - timedelta(days=max(keep_days, max_age_days) + 2)
        dead = [k for k, v in self.items.items() if parse_iso(v.first_seen) < horizon]
        for k in dead:
            item = self.items.pop(k)
            self._title_index.pop(title_key(item.title), None)
            self.tags.pop(k, None)
        # Labels for items no longer stored are useless.
        for k in [k for k in self.tags if k not in self.items]:
            self.tags.pop(k)
        cutoff = (now - timedelta(days=STATS_DAYS)).date().isoformat()
        for src in self.sources.values():
            src["days"] = {d: v for d, v in src.get("days", {}).items() if d >= cutoff}
        months = sorted(self.usage)
        for m in months[:-12]:
            self.usage.pop(m)

    def save(self) -> None:
        _save(self.dir / "items.json", {k: v.to_dict() for k, v in self.items.items()})
        _save(self.dir / "tags.json", self.tags)
        _save(self.dir / "sources.json", self.sources)
        _save(self.dir / "usage.json", self.usage)


def now_iso(now: datetime) -> str:
    return iso(now)
