"""Load and validate config.yaml and feeds.yaml."""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass, field

import yaml


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Feed:
    id: str
    name: str
    url: str
    category: str
    max_items: int
    enabled: bool = True

    @property
    def is_google_news(self) -> bool:
        return "news.google.com" in self.url


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    mode: str  # "dial" or "pass_all"
    block_flags: frozenset[str]
    max_items: int
    untagged: str  # "hide" or "show"
    default_feed_items: int


@dataclass(frozen=True)
class DialLevel:
    level: int
    name: str
    negative_min: int
    priority_min: int | None


@dataclass
class Settings:
    title: str
    base_url: str
    model: str
    batch_size: int
    input_price: float
    output_price: float
    monthly_item_cap: int
    timeout: float
    workers: int
    user_agent: str
    max_age_days: float
    keep_days: float
    default_level: int
    emergency_floor: int
    levels: dict[int, DialLevel]
    priorities: frozenset[str]
    categories: dict[str, Category]
    block_keywords: list[re.Pattern]
    allow_keywords: list[re.Pattern]
    rewrite_titles: bool
    topics: list[str]
    flags: dict[str, str]
    timezone: str = "UTC"
    cap_exempt_significance: int = 4
    feeds: list[Feed] = field(default_factory=list)


def google_news_url(query: str) -> str:
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "feed"


def _compile(patterns, where):
    out = []
    for p in patterns or []:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error as exc:
            raise ConfigError(f"Bad regular expression in {where}: {p!r} ({exc})") from exc
    return out


def load_settings(config_path: str, feeds_path: str) -> Settings:
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    with open(feeds_path, encoding="utf-8") as fh:
        feeds_cfg = yaml.safe_load(fh) or {}

    site = cfg.get("site", {})
    model = cfg.get("model", {})
    fetch = cfg.get("fetch", {})
    window = cfg.get("window", {})
    dial = cfg.get("dial", {})

    topics = list(cfg.get("topics") or [])
    if "other" not in topics:
        topics.append("other")
    flags = dict(cfg.get("flags") or {})

    default_block = frozenset(cfg.get("default_block_flags") or [])
    unknown = default_block - flags.keys()
    if unknown:
        raise ConfigError(f"default_block_flags lists undefined flags: {sorted(unknown)}")

    categories: dict[str, Category] = {}
    for key, c in (cfg.get("categories") or {}).items():
        mode = c.get("mode", "dial")
        if mode not in ("dial", "pass_all"):
            raise ConfigError(f"Category {key}: mode must be 'dial' or 'pass_all', got {mode!r}")
        untagged = c.get("untagged", "hide")
        if untagged not in ("hide", "show"):
            raise ConfigError(f"Category {key}: untagged must be 'hide' or 'show'")
        block = frozenset(c["block_flags"]) if "block_flags" in c else default_block
        bad = block - flags.keys()
        if bad:
            raise ConfigError(f"Category {key}: block_flags lists undefined flags: {sorted(bad)}")
        categories[key] = Category(
            key=key,
            label=c.get("label", key.replace("_", " ").title()),
            mode=mode,
            block_flags=block,
            max_items=int(c.get("max_items", 20)),
            untagged=untagged,
            default_feed_items=int(c.get("default_feed_items", 15)),
        )
    if not categories:
        raise ConfigError("config.yaml defines no categories")

    levels: dict[int, DialLevel] = {}
    for lvl, spec in (dial.get("levels") or {}).items():
        lvl = int(lvl)
        levels[lvl] = DialLevel(
            level=lvl,
            name=spec.get("name", f"Level {lvl}"),
            negative_min=int(spec.get("negative_min_significance", 3)),
            priority_min=(int(spec["priority_min_significance"])
                          if spec.get("priority_min_significance") is not None else None),
        )
    if not levels:
        raise ConfigError("config.yaml defines no dial levels")
    default_level = int(dial.get("default", min(levels)))
    if default_level not in levels:
        raise ConfigError(f"dial.default ({default_level}) is not one of the defined levels")

    priorities = frozenset(cfg.get("priorities") or [])
    bad = priorities - set(topics)
    if bad:
        raise ConfigError(f"priorities lists topics that aren't in the topic list: {sorted(bad)}")

    keywords = cfg.get("keywords") or {}

    settings = Settings(
        title=site.get("title", "Leeward"),
        base_url=(os.environ.get("LEEWARD_BASE_URL") or site.get("base_url") or "").rstrip("/"),
        model=model.get("name", "claude-haiku-5-5"),
        batch_size=max(1, int(model.get("batch_size", 25))),
        input_price=float(model.get("input_price_per_mtok", 0.10)),
        output_price=float(model.get("output_price_per_mtok", 0.50)),
        monthly_item_cap=int(model.get("monthly_item_cap", 40000)),
        timeout=float(fetch.get("timeout_seconds", 20)),
        workers=max(1, int(fetch.get("workers", 8))),
        user_agent=fetch.get("user_agent", "Leeward/0.1 (personal RSS filter)"),
        max_age_days=float(window.get("max_age_days", 3)),
        keep_days=float(window.get("keep_days", 2)),
        default_level=default_level,
        emergency_floor=int(dial.get("emergency_floor", 5)),
        levels=dict(sorted(levels.items())),
        priorities=priorities,
        categories=categories,
        block_keywords=_compile(keywords.get("block"), "keywords.block"),
        allow_keywords=_compile(keywords.get("allow"), "keywords.allow"),
        rewrite_titles=bool(cfg.get("rewrite_titles", True)),
        topics=topics,
        flags=flags,
        timezone=site.get("timezone", "UTC"),
        cap_exempt_significance=int(dial.get("always_include_significance", 4)),
    )

    seen_ids: set[str] = set()
    for raw in feeds_cfg.get("feeds") or []:
        name = raw.get("name")
        if not name:
            raise ConfigError(f"A feed is missing a name: {raw}")
        cat = raw.get("category")
        if cat not in categories:
            raise ConfigError(f"Feed {name!r} uses unknown category {cat!r}")
        if raw.get("google_news"):
            url = google_news_url(raw["google_news"])
        elif raw.get("url"):
            url = raw["url"]
        else:
            raise ConfigError(f"Feed {name!r} needs either url or google_news")
        fid = slugify(name)
        base, n = fid, 2
        while fid in seen_ids:
            fid = f"{base}-{n}"
            n += 1
        seen_ids.add(fid)
        settings.feeds.append(Feed(
            id=fid,
            name=name,
            url=url,
            category=cat,
            max_items=int(raw.get("max_items", categories[cat].default_feed_items)),
            enabled=bool(raw.get("enabled", True)),
        ))
    return settings
