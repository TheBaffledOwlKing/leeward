import dataclasses
import xml.etree.ElementTree as ET
from datetime import timedelta

from leeward.build import build
from leeward.state import State
from leeward.tagger import FakeTagger

from conftest import NOW, fixture_results


def titles(path):
    root = ET.parse(path).getroot()
    return [i.findtext("title") for i in root.iter("item")]


def run(settings, tmp_path, now=NOW, tagger=None, results=None):
    tagger = tagger or FakeTagger(settings)
    return build(settings, tagger, state_dir=tmp_path / "state", out_dir=tmp_path / "public", now=now,
                 fetch_results=results if results is not None else fixture_results(settings))


def test_end_to_end_outputs(settings, tmp_path):
    report = run(settings, tmp_path)
    out = tmp_path / "public"
    for name in ["index.html", "hidden.html", "health.html", "subscriptions.opml",
                 "feeds/digest-0.xml", "feeds/digest-3.xml", "feeds/news.xml", "feeds/pictures.xml"]:
        assert (out / name).exists(), name

    assert report.fetched_errors == 1          # the broken feed
    # 3 sports + 4 fresh news + 1 KSL story (its park story duplicates the news feed's) + 2 pictures
    assert report.new_items == 10
    level0 = titles(out / "feeds/digest-0.xml")
    level3 = titles(out / "feeds/digest-3.xml")

    # Wholesome things show at every level.
    for t in ["Adorable otter pup learns to swim", "Stunning aurora over the Wasatch", "City council approves new park"]:
        assert t in level0 and t in level3
    # Historic news breaks through even on a "Not today" day.
    assert any("Supreme Court" in t for t in level0)
    # Routine bad news only at "Everything".
    assert "Factory fire injures workers" not in level0
    assert "Factory fire injures workers" in level3
    # Outrage bait and betting odds never show.
    for t in level0 + level3:
        assert "slams" not in t and "odds" not in t
    # Sports shows its bad news too.
    assert "Rams lose star receiver to injury" in level0
    # The stale story was skipped and the Google News copy of the park story was deduped.
    assert "Old story from weeks ago" not in level3
    assert level3.count("City council approves new park") == 1

    hidden = (out / "hidden.html").read_text()
    assert "flagged outrage bait" in hidden and "<details>" in hidden

    opml = ET.parse(out / "subscriptions.opml").getroot()
    assert len(list(opml.iter("outline"))) > 5


def test_second_run_does_not_relabel(settings, tmp_path):
    run(settings, tmp_path)
    tagger = FakeTagger(settings)
    report = run(settings, tmp_path, now=NOW + timedelta(hours=2), tagger=tagger)
    assert report.new_items == 0
    assert report.labeled == 0
    assert tagger.usage.items == 0


def test_failing_feed_shows_on_health_page(settings, tmp_path):
    for hours in (0, 2, 4):
        run(settings, tmp_path, now=NOW + timedelta(hours=hours))
    health = (tmp_path / "public" / "health.html").read_text()
    assert "Broken Feed" in health and "Failing (3 runs in a row)" in health


def test_old_items_age_out(settings, tmp_path):
    run(settings, tmp_path)
    later = NOW + timedelta(days=10)
    run(settings, tmp_path, now=later, results=[])
    state = State(tmp_path / "state")
    assert state.items == {} and state.tags == {}
    assert titles(tmp_path / "public" / "feeds" / "digest-3.xml") == []


def test_monthly_cap_stops_labeling(settings, tmp_path):
    settings.monthly_item_cap = 3
    report = run(settings, tmp_path)
    assert report.labeled == 3
    assert report.waiting == report.new_items - 3
    report2 = run(settings, tmp_path, now=NOW + timedelta(hours=1))
    assert report2.capped and report2.labeled == 0


def test_category_caps_keep_sports_from_dominating(settings, tmp_path):
    settings.categories["sports"] = dataclasses.replace(settings.categories["sports"], max_items=1)
    run(settings, tmp_path)
    root = ET.parse(tmp_path / "public" / "feeds" / "digest-3.xml").getroot()
    cats = [i.findtext("category") for i in root.iter("item")]
    assert cats.count("sports") == 1
