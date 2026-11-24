from pathlib import Path

import feedparser
import pytest

from leeward.config import ConfigError, Feed, google_news_url, load_settings
from leeward.items import normalize_entry, strip_html, title_key

from conftest import FIXTURES, NOW, ROOT


def test_real_config_and_feed_list_load():
    s = load_settings(str(ROOT / "config.yaml"), str(ROOT / "feeds.yaml"))
    assert s.model == "claude-haiku-5-5"
    assert set(s.levels) == {0, 1, 2, 3}
    cats = {f.category for f in s.feeds if f.enabled}
    assert cats == set(s.categories)
    # Good news + science + pictures outnumber sports in the feed list itself.
    count = lambda c: sum(1 for f in s.feeds if f.enabled and f.category == c)
    assert count("good_news") + count("pictures") + count("science") > 2 * count("sports")
    assert len({f.id for f in s.feeds}) == len(s.feeds)
    rams = next(f for f in s.feeds if f.name == "Rams (Google News)")
    assert rams.is_google_news and "%22Los+Angeles+Rams%22" in rams.url


def test_google_news_url():
    url = google_news_url('"Utah Mammoth" when:1d')
    assert url.startswith("https://news.google.com/rss/search?q=%22Utah+Mammoth%22+when%3A1d")
    assert "ceid=US%3Aen" in url


def test_bad_category_is_rejected(tmp_path):
    feeds = tmp_path / "feeds.yaml"
    feeds.write_text("feeds:\n  - name: X\n    category: nope\n    url: https://x.invalid/\n")
    with pytest.raises(ConfigError, match="unknown category"):
        load_settings(str(ROOT / "config.yaml"), str(feeds))


def test_bad_priority_is_rejected(tmp_path):
    cfg = (ROOT / "config.yaml").read_text().replace("  - local_utah\n", "  - not_a_topic\n", 1)
    path = tmp_path / "config.yaml"
    path.write_text(cfg)
    with pytest.raises(ConfigError, match="priorities"):
        load_settings(str(path), str(ROOT / "feeds.yaml"))


def test_strip_html_and_image():
    text, img = strip_html('<p>Hi <b>there</b></p><script>bad()</script><img src="https://x.test/a.png">')
    assert text == "Hi there"
    assert img == "https://x.test/a.png"


def test_google_news_titles_are_cleaned():
    feed = Feed("g", "G", google_news_url("x"), "news", 10)
    parsed = feedparser.parse((FIXTURES / "google.xml").read_bytes())
    item = normalize_entry(parsed.entries[0], feed, NOW)
    assert item.title == "City council approves new park"
    assert item.publisher == "KSL.com"
    assert item.summary == ""


def test_images_from_media_and_inline_html():
    feed = Feed("p", "P", "file://x", "pictures", 10)
    parsed = feedparser.parse((FIXTURES / "pictures.atom").read_bytes())
    otter, aurora = (normalize_entry(e, feed, NOW) for e in parsed.entries)
    assert otter.image == "https://example.com/otter.jpg"
    assert aurora.image == "https://example.com/aurora.webp"
    assert aurora.summary == "Green skies."


def test_title_key_matches_across_punctuation():
    assert title_key("Rams win!") == title_key("rams   WIN")
