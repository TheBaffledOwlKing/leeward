"""Regression tests for the issues found on the first live run."""

import xml.etree.ElementTree as ET
from datetime import timedelta

import feedparser
import httpx

from leeward.build import build
from leeward.config import Feed
from leeward.fetch import _parse, fetch_one
from leeward.items import normalize_entry
from leeward.pages import source_status
from leeward.tagger import FakeTagger

from conftest import NOW, fixture_results


def guids(path):
    return [g.text for g in ET.parse(path).getroot().iter("guid")]


def test_every_output_feed_has_its_own_guids(settings, tmp_path):
    """FeedFlow keeps one copy of an article per guid. If digest-1 and the News section
    share guids, the article only shows in whichever feed loaded first."""
    build(settings, FakeTagger(settings), state_dir=tmp_path / "s", out_dir=tmp_path / "p",
          now=NOW, fetch_results=fixture_results(settings))
    feeds = sorted((tmp_path / "p" / "feeds").glob("*.xml"))
    seen = {}
    for path in feeds:
        for g in guids(path):
            assert g not in seen, f"{g} appears in both {seen[g]} and {path.name}"
            seen[g] = path.name
    assert len(seen) > 20


def test_opml_puts_each_level_in_its_own_folder(settings, tmp_path):
    build(settings, FakeTagger(settings), state_dir=tmp_path / "s", out_dir=tmp_path / "p",
          now=NOW, fetch_results=fixture_results(settings))
    body = ET.parse(tmp_path / "p" / "subscriptions.opml").getroot().find("body")
    folders = [o.get("text") for o in body.findall("outline")]
    assert len(folders) == len(settings.levels) + 1
    assert folders[-1] == "Leeward sections"


def test_significant_items_are_never_cut_by_section_caps(settings, tmp_path):
    import dataclasses
    settings.categories["news"] = dataclasses.replace(settings.categories["news"], max_items=1)
    build(settings, FakeTagger(settings), state_dir=tmp_path / "s", out_dir=tmp_path / "p",
          now=NOW, fetch_results=fixture_results(settings))
    root = ET.parse(tmp_path / "p" / "feeds" / "digest-3.xml").getroot()
    titles = [i.findtext("title") for i in root.iter("item") if i.findtext("category") == "news"]
    # One ordinary news slot, plus the significance-5 Supreme Court story, which is exempt.
    assert len(titles) == 2
    assert any("Supreme Court" in t for t in titles)


def test_index_shows_negative_counts_per_level(settings, tmp_path):
    report = build(settings, FakeTagger(settings), state_dir=tmp_path / "s", out_dir=tmp_path / "p",
                   now=NOW, fetch_results=fixture_results(settings))
    assert report.levels[3]["negative"] > report.levels[0]["negative"]
    html = (tmp_path / "p" / "index.html").read_text()
    assert f"{report.levels[3]['negative']} negative" in html


APOD_LIKE = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>APOD</title>
<link>https://apod.nasa.gov/</link><description>x</description>
<item><title>Galaxy Today</title><link>https://apod.nasa.gov/apod/astropix.html</link><description>a</description></item>
</channel></rss>"""


def test_feeds_without_guids_still_get_new_items_daily():
    feed = Feed("apod", "APOD", "https://apod.nasa.gov/apod.rss", "pictures", 5)
    day1 = normalize_entry(feedparser.parse(APOD_LIKE).entries[0], feed, NOW)
    day2 = normalize_entry(feedparser.parse(APOD_LIKE.replace(b"Galaxy Today", b"Comet Tomorrow")).entries[0],
                           feed, NOW + timedelta(days=1))
    assert day1.link == day2.link
    assert day1.id != day2.id


def test_health_explains_duplicate_feeds(settings, tmp_path):
    # A second copy of the news fixture under another name: every story is a duplicate.
    dup = Feed("dupe", "News Copy", "file://dupe", "news", 15)
    results = fixture_results(settings)
    from conftest import FIXTURES
    results.append(_parse(dup, (FIXTURES / "news.xml").read_bytes()))
    settings.feeds.append(dup)
    build(settings, FakeTagger(settings), state_dir=tmp_path / "s", out_dir=tmp_path / "p",
          now=NOW, fetch_results=results)
    health = (tmp_path / "p" / "health.html").read_text()
    assert "already coming in through Test News" in health


def test_health_explains_stale_feeds():
    src = {"last_ok": NOW.isoformat(), "consecutive_failures": 0, "days": {},
           "last_fetch": {"entries": 4, "added": 0, "too_old": 4, "duplicates": 0,
                          "newest": (NOW - timedelta(days=12)).isoformat()}}
    css, msg = source_status(src, NOW, 3)
    assert css == "warn" and "12 days old" in msg


def test_health_names_blocked_sites():
    src = {"consecutive_failures": 1, "last_ok": "",
           "last_error": "HTTP 403: the site refuses automated requests from GitHub's servers"}
    css, msg = source_status(src, NOW)
    assert css == "bad" and msg.startswith("Blocked.")


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


FEED_OK = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title><link>https://x/</link>
<description>d</description><item><title>Hi</title><link>https://x/1</link></item></channel></rss>"""


def test_fetch_retries_once_on_temporary_errors():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, content=FEED_OK)

    feed = Feed("f", "F", "https://x.invalid/feed", "news", 5)
    res = fetch_one(feed, _client(handler), retry_pause=0)
    assert res.status == "ok" and len(calls) == 2


def test_fetch_does_not_retry_when_blocked():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403)

    feed = Feed("f", "F", "https://x.invalid/feed", "news", 5)
    res = fetch_one(feed, _client(handler), retry_pause=0)
    assert res.status == "error" and "refuses automated" in res.error and len(calls) == 1


def test_fetch_reports_repeated_timeouts():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    feed = Feed("f", "F", "https://x.invalid/feed", "news", 5)
    res = fetch_one(feed, _client(handler), retry_pause=0)
    assert res.status == "error" and res.error.startswith("Timed out, then again on retry")
