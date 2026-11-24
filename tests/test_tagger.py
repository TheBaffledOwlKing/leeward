import json
from types import SimpleNamespace

from leeward.items import Item
from leeward.tagger import ClaudeTagger, build_system_prompt, build_tool, clean_label

from conftest import NOW


def make_items(n, category="news"):
    now = NOW.isoformat()
    return [Item(id=f"id{i}", source_id="s", source_name="S", category=category, title=f"Headline {i}",
                 link=f"https://x.invalid/{i}", summary="Summary", image="", published=now, first_seen=now)
            for i in range(n)]


class FakeMessages:
    """Mimics client.messages.create, answering with a tool_use block."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs["messages"][0]["content"].split("\n", 1)[1])
        ids = [p["id"] for p in payload]
        labels = self.behaviour(ids, len(self.calls))
        block = SimpleNamespace(type="tool_use", input={"labels": labels}, name="record_labels")
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200,
                                cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return SimpleNamespace(content=[block], usage=usage)


def label(i, **over):
    base = {"id": i, "tone": "neutral", "significance": 1, "topics": ["other"], "flags": [], "neutral_title": ""}
    base.update(over)
    return base


def test_prompt_and_tool_reflect_config(settings):
    prompt = build_system_prompt(settings)
    assert "same standards regardless of political party" in prompt
    assert "conflict_callout" in prompt and "democracy_elections" in prompt
    tool = build_tool(settings)
    schema = tool["input_schema"]["properties"]["labels"]["items"]["properties"]
    assert schema["topics"]["items"]["enum"] == settings.topics
    assert set(schema["flags"]["items"]["enum"]) == set(settings.flags)


def test_clean_label_coerces_bad_values(settings):
    out = clean_label({"tone": "negative", "significance": "9", "topics": ["made_up", "courts_law"],
                       "flags": ["sponsored", "nonsense"], "neutral_title": "  Plain title  "}, settings)
    assert out["significance"] == 5
    assert out["topics"] == ["courts_law"]
    assert out["flags"] == ["sponsored"]
    assert out["neutral_title"] == "Plain title"
    assert clean_label({"tone": "angry", "significance": 1}, settings) is None


def test_batches_and_usage(settings):
    settings.batch_size = 10
    fake = FakeMessages(lambda ids, n: [label(i) for i in ids])
    tagger = ClaudeTagger(settings, client=SimpleNamespace(messages=fake))
    out = tagger.tag(make_items(25))
    assert len(out) == 25
    assert len(fake.calls) == 3
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["tool_choice"] == {"type": "tool", "name": "record_labels"}
    assert tagger.usage.input_tokens == 3000 and tagger.usage.output_tokens == 600


def test_skipped_items_get_one_retry(settings):
    def behaviour(ids, call_no):
        return [label(i) for i in ids[:-2]] if call_no == 1 else [label(i) for i in ids]
    fake = FakeMessages(behaviour)
    tagger = ClaudeTagger(settings, client=SimpleNamespace(messages=fake))
    out = tagger.tag(make_items(5))
    assert len(out) == 5 and len(fake.calls) == 2


def test_api_failure_leaves_items_for_next_run(settings):
    class Boom:
        def create(self, **kwargs):
            raise RuntimeError("no credit")
    tagger = ClaudeTagger(settings, client=SimpleNamespace(messages=Boom()))
    assert tagger.tag(make_items(3)) == {}
    assert tagger.usage.errors == 1


def test_labels_for_unknown_ids_are_ignored(settings):
    fake = FakeMessages(lambda ids, n: [label("someone-else")] + [label(i) for i in ids])
    tagger = ClaudeTagger(settings, client=SimpleNamespace(messages=fake))
    out = tagger.tag(make_items(2))
    assert set(out) == {"id0", "id1"}
