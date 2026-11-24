"""Command line entry point.

  python -m leeward build                 fetch, label with Claude, write public/
  python -m leeward build --fake-tagger   same, but label with keywords (no API key, no cost)
  python -m leeward build --limit 50      label at most 50 new items this run (handy for a first test)
  python -m leeward check-feeds           fetch every feed and report which ones work (no labeling)
  python -m leeward try "Some headline"   show how Claude labels a headline and what each dial level does
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from .config import ConfigError, load_settings


def _settings(args):
    try:
        return load_settings(args.config, args.feeds)
    except (ConfigError, FileNotFoundError) as exc:
        sys.exit(f"Config problem: {exc}")


def cmd_build(args) -> int:
    from .build import build
    from .tagger import ClaudeTagger, FakeTagger

    settings = _settings(args)
    if args.fake_tagger:
        tagger = FakeTagger(settings)
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY isn't set. Set it, or run with --fake-tagger to test without the API.")
        tagger = ClaudeTagger(settings)
    report = build(settings, tagger, state_dir=args.state, out_dir=args.out, label_limit=args.limit)
    shown = ", ".join(f"level {k}: {v}" for k, v in report.shown.items())
    print(f"Feeds: {report.fetched_ok} ok, {report.fetched_errors} failed. "
          f"New items: {report.new_items}. Labeled: {report.labeled}"
          f"{f', still waiting: {report.waiting}' if report.waiting else ''}. Showing {shown}.")
    if report.capped:
        print("Monthly label cap reached; see model.monthly_item_cap in config.yaml.")
    if report.tagger_errors and not report.labeled and report.waiting:
        # Every labeling call failed (bad key, no credit, API down). Fail loudly so the
        # Actions run shows red, but only after the outputs and state were written.
        print("Every labeling call failed. Check the API key and account balance.", file=sys.stderr)
        return 2
    return 0


def cmd_check(args) -> int:
    from .fetch import fetch_all

    settings = _settings(args)
    feeds = [f for f in settings.feeds if f.enabled]
    results = fetch_all(feeds, {}, timeout=settings.timeout, workers=settings.workers,
                        user_agent=settings.user_agent)
    bad = 0
    for res in results:
        if res.status == "ok":
            newest = ""
            for e in res.entries:
                st = e.get("published_parsed") or e.get("updated_parsed")
                if st:
                    stamp = datetime(*st[:6], tzinfo=timezone.utc).date().isoformat()
                    newest = max(newest, stamp)
            print(f"  OK    {res.feed.name:<45} {len(res.entries):>3} entries, newest {newest or 'undated'}")
        else:
            bad += 1
            print(f"  FAIL  {res.feed.name:<45} {res.error}")
    print(f"{len(results) - bad} of {len(results)} feeds working.")
    return 1 if bad else 0


def cmd_try(args) -> int:
    from .items import Item, iso
    from .rules import decide, display_title
    from .tagger import ClaudeTagger, FakeTagger

    settings = _settings(args)
    category = args.category
    if category not in settings.categories:
        sys.exit(f"Unknown category {category!r}. Choose from: {', '.join(settings.categories)}")
    now = iso(datetime.now(timezone.utc))
    item = Item(id="try", source_id="try", source_name=args.source, category=category,
                title=args.headline, link="https://example.invalid/", summary=args.summary,
                image="", published=now, first_seen=now)
    tagger = FakeTagger(settings) if args.fake_tagger else ClaudeTagger(settings)
    tags = tagger.tag([item]).get("try")
    if not tags:
        sys.exit("No label came back.")
    print(f"tone={tags['tone']} significance={tags['significance']} topics={tags['topics']} flags={tags['flags']}")
    if tags["neutral_title"]:
        print(f"rewritten title: {display_title(item, tags, settings)}")
    for lvl, level in settings.levels.items():
        d = decide(item, tags, level, settings)
        print(f"  level {lvl} {level.name:<11} {'SHOW' if d.visible else 'hide'}  ({d.reason})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="leeward", description="Calm, filtered RSS feeds.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--feeds", default="feeds.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="fetch, label, and write the feeds")
    b.add_argument("--state", default="state")
    b.add_argument("--out", default="public")
    b.add_argument("--fake-tagger", action="store_true", help="label with keywords instead of Claude")
    b.add_argument("--limit", type=int, default=None, help="label at most this many new items")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("check-feeds", help="fetch every feed and report which work")
    c.set_defaults(func=cmd_check)

    t = sub.add_parser("try", help="label one headline and show what each dial level does")
    t.add_argument("headline")
    t.add_argument("--summary", default="")
    t.add_argument("--category", default="news")
    t.add_argument("--source", default="Test")
    t.add_argument("--fake-tagger", action="store_true")
    t.set_defaults(func=cmd_try)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
