import xml.etree.ElementTree as ET

from leeward.items import Item
from leeward.render import opml, rss

from conftest import NOW


def test_rss_survives_awkward_text():
    now = NOW.isoformat()
    item = Item(id="a&b", source_id="s", source_name="Tom & Jerry's <News>", category="news",
                title='Rock & Roll ]]> "quotes" \x0b control', link="https://x.invalid/?a=1&b=2",
                summary="<script>alert(1)</script> & more", image="https://x.invalid/i.png?x=1&y=2",
                published=now, first_seen=now)
    xml = rss("T & T", "D", "https://x.invalid", "https://x.invalid/feed.xml",
              [(item, {"topics": ["other"]}, "Rewritten <title>")], NOW)
    root = ET.fromstring(xml.encode())
    it = root.find("channel/item")
    assert it.findtext("title") == "Rewritten <title>"
    assert it.findtext("link") == "https://x.invalid/?a=1&b=2"
    body = it.findtext("description")
    assert "&lt;script&gt;" in body          # summary HTML is escaped, not executed
    assert "Original headline" in body       # shown because the title was rewritten
    assert it.find("enclosure").get("type") == "image/png"


def test_opml_groups_by_folder():
    xml = opml("Feeds", [("A", "one", "https://x/1"), ("A", "two", "https://x/2"), ("B", "three", "https://x/3")])
    root = ET.fromstring(xml.encode())
    folders = root.findall("body/outline")
    assert [f.get("text") for f in folders] == ["A", "B"]
    assert len(folders[0].findall("outline")) == 2
