from datetime import datetime, timezone
from pathlib import Path

import pytest

from leeward.config import Feed, google_news_url, load_settings
from leeward.fetch import _parse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings():
    s = load_settings(str(ROOT / "config.yaml"), str(ROOT / "feeds.yaml"))
    s.feeds = [
        Feed("rams", "Test Rams", "file://" + str(FIXTURES / "sports.xml"), "sports", 15),
        Feed("news", "Test News", "file://" + str(FIXTURES / "news.xml"), "news", 15),
        Feed("ksl", "KSL (Google News)", google_news_url("site:ksl.com when:1d"), "news", 10),
        Feed("pics", "Test Pictures", "file://" + str(FIXTURES / "pictures.atom"), "pictures", 15),
        Feed("broken", "Broken Feed", "file://" + str(FIXTURES / "broken.xml"), "good_news", 15),
    ]
    return s


def fixture_results(settings):
    """Fetch results for the test feeds. The Google News feed is read from a file
    but keeps its google URL so Google-specific cleanup applies."""
    out = []
    files = {"rams": "sports.xml", "news": "news.xml", "ksl": "google.xml",
             "pics": "pictures.atom", "broken": "broken.xml"}
    for feed in settings.feeds:
        out.append(_parse(feed, (FIXTURES / files[feed.id]).read_bytes()))
    return out


@pytest.fixture
def results(settings):
    return fixture_results(settings)
