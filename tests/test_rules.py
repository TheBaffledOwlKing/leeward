from leeward.items import Item
from leeward.rules import decide, display_title

from conftest import NOW


def make_item(category="news", title="Something happened"):
    now = NOW.isoformat()
    return Item(id="x", source_id="s", source_name="S", category=category, title=title,
                link="https://x.invalid/", summary="", image="", published=now, first_seen=now)


def tags(tone="negative", sig=2, topics=("other",), flags=(), neutral=""):
    return {"tone": tone, "significance": sig, "topics": list(topics), "flags": list(flags),
            "neutral_title": neutral}


def visible_levels(settings, item, t):
    return [lvl for lvl, level in settings.levels.items() if decide(item, t, level, settings).visible]


def test_uplifting_and_neutral_show_at_every_level(settings):
    item = make_item("good_news")
    assert visible_levels(settings, item, tags("uplifting", 1)) == [0, 1, 2, 3]
    assert visible_levels(settings, item, tags("neutral", 0)) == [0, 1, 2, 3]


def test_routine_negative_news_only_at_everything(settings):
    assert visible_levels(settings, make_item(), tags("negative", 2)) == [3]


def test_major_negative_news(settings):
    # Significance 4: Essentials and up. Not on a "Not today" day.
    assert visible_levels(settings, make_item(), tags("negative", 4)) == [1, 2, 3]


def test_historic_news_breaks_through_even_at_level_zero(settings):
    assert visible_levels(settings, make_item(), tags("negative", 5)) == [0, 1, 2, 3]


def test_priority_topics_get_a_lower_bar(settings):
    item = make_item()
    plain = tags("negative", 3, topics=["economy_money"])
    priority = tags("negative", 3, topics=["democracy_elections"])
    assert visible_levels(settings, item, plain) == [2, 3]
    assert visible_levels(settings, item, priority) == [1, 2, 3]
    d = decide(item, priority, settings.levels[1], settings)
    assert "priority topic" in d.reason


def test_blocked_flags_hide_even_good_items(settings):
    item = make_item("good_news")
    assert visible_levels(settings, item, tags("uplifting", 1, flags=["sponsored"])) == []
    assert visible_levels(settings, item, tags("uplifting", 1, flags=["clickbait"])) == [0, 1, 2, 3]


def test_sports_pass_everything_but_betting(settings):
    item = make_item("sports")
    assert visible_levels(settings, item, tags("negative", 1)) == [0, 1, 2, 3]
    assert visible_levels(settings, item, tags("neutral", 0, flags=["betting_odds"])) == []
    # Sports doesn't block drama flags; it's all Rams news.
    assert visible_levels(settings, item, tags("negative", 1, flags=["conflict_callout"])) == [0, 1, 2, 3]


def test_unlabeled_items_follow_category_policy(settings):
    assert visible_levels(settings, make_item("news"), None) == []
    assert visible_levels(settings, make_item("sports"), None) == [0, 1, 2, 3]


def test_keyword_overrides(settings):
    import re
    settings.block_keywords = [re.compile("mock draft", re.I)]
    settings.allow_keywords = [re.compile("puka", re.I)]
    assert visible_levels(settings, make_item("sports", "Latest mock draft"), tags("neutral", 1)) == []
    assert visible_levels(settings, make_item("news", "Puka injured"), tags("negative", 1)) == [0, 1, 2, 3]


def test_emergency_floor_can_be_disabled(settings):
    settings.emergency_floor = 6
    assert visible_levels(settings, make_item(), tags("negative", 5)) == [1, 2, 3]


def test_neutral_title_is_used_when_present(settings):
    item = make_item(title="You WON'T believe what this city did")
    assert display_title(item, tags("uplifting", 1, neutral="City opens free library"), settings) == "City opens free library"
    assert display_title(item, tags("uplifting", 1), settings) == item.title
    settings.rewrite_titles = False
    assert display_title(item, tags("uplifting", 1, neutral="City opens free library"), settings) == item.title
