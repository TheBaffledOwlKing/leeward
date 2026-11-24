"""Label items with Claude (or a keyword-based stand-in for offline testing).

Each item gets:
  tone           uplifting | neutral | negative
  significance   0-5, scored by real-world impact
  topics         1-3 topics from the configured list
  flags          zero or more configured flags
  neutral_title  a plain rewrite, only when the original headline is sensational
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from .config import Settings
from .items import Item

log = logging.getLogger(__name__)

TONES = ("uplifting", "neutral", "negative")
LABEL_VERSION = 1  # bump when the prompt changes enough to warrant relabeling


SIGNIFICANCE_RUBRIC = """\
5 = Historic or emergency. An event most people will remember for years, or an immediate threat to safety in the reader's area (Utah). Examples: a landmark court decision that reverses long-standing precedent or changes a constitutional right; a declared national emergency; the start of a major war; a pandemic declaration; a major disaster in Utah.
4 = Major and lasting. A concrete change that affects millions of people for years. Examples: a major federal law passed; a major court ruling; a large economic shock; a serious disaster elsewhere in the US.
3 = Notable. A real development with consequences, but incremental or limited in scope. Examples: a new agency rule; a significant state law; a large layoff; an important election outside the US.
2 = Routine. Ordinary news flow: statements, accusations, controversies, polls, hearings, investigations, daily political back-and-forth, most crime stories.
1 = Minor. Niche, local to somewhere else, or human-interest with little wider effect.
0 = Trivial. Gossip, entertainment chatter, listicles, filler."""


def build_system_prompt(settings: Settings) -> str:
    flags = "\n".join(f"- {name}: {desc}" for name, desc in settings.flags.items())
    topics = ", ".join(settings.topics)
    return f"""You label items for a personal news reader that filters out needless negativity while keeping the reader informed about things that genuinely matter.

Label every item independently. Be impartial: apply exactly the same standards regardless of political party, ideology, country, or which side an event helps or hurts. Judge only from the title, summary, source, and section you are given.

TONE
- uplifting: good news, kindness, rescue, recovery, discovery, beauty, achievement, humor, cute or wholesome content
- neutral: informational, mixed, or matter-of-fact (schedules, previews, explainers, most science)
- negative: harm, death, loss, conflict, threat, wrongdoing, failure, or fear is the main subject
A sports team losing, an injury, or a bad trade is negative for that item.

SIGNIFICANCE (0-5): rate real-world impact, not how upsetting or loud a story is.
Weigh scope (how many people), permanence (lasting vs. reversible), and concreteness (an actual change vs. talk about a possible one).
{SIGNIFICANCE_RUBRIC}
Rate uplifting and neutral items on the same scale; most will be 0-2.

TOPICS: choose 1 to 3 from this list only: {topics}

FLAGS: include every flag that applies, otherwise an empty list.
{flags}

NEUTRAL_TITLE: only when the original headline is sensational, clickbait, vague, or misleading, write a plain factual headline of at most 14 words. Otherwise return an empty string.

Return one label for every item id you are given, using the record_labels tool."""


def build_tool(settings: Settings) -> dict:
    return {
        "name": "record_labels",
        "description": "Record the labels for every item in the batch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "tone": {"type": "string", "enum": list(TONES)},
                            "significance": {"type": "integer", "minimum": 0, "maximum": 5},
                            "topics": {"type": "array", "items": {"type": "string", "enum": settings.topics}},
                            "flags": {"type": "array", "items": {"type": "string", "enum": list(settings.flags)}},
                            "neutral_title": {"type": "string"},
                        },
                        "required": ["id", "tone", "significance", "topics", "flags", "neutral_title"],
                    },
                }
            },
            "required": ["labels"],
        },
    }


def item_payload(item: Item, settings: Settings) -> dict:
    return {
        "id": item.id,
        "section": settings.categories[item.category].label,
        "source": item.publisher or item.source_name,
        "title": item.title,
        "summary": item.summary[:300],
    }


def clean_label(raw: dict, settings: Settings) -> dict | None:
    """Validate one label from the model; return None if unusable."""
    tone = raw.get("tone")
    if tone not in TONES:
        return None
    try:
        sig = int(raw.get("significance"))
    except (TypeError, ValueError):
        return None
    topics = [t for t in (raw.get("topics") or []) if t in settings.topics][:3] or ["other"]
    flags = sorted({f for f in (raw.get("flags") or []) if f in settings.flags})
    neutral = (raw.get("neutral_title") or "").strip()
    if len(neutral) > 160:
        neutral = neutral[:160].rsplit(" ", 1)[0]
    return {
        "tone": tone,
        "significance": max(0, min(5, sig)),
        "topics": topics,
        "flags": flags,
        "neutral_title": neutral,
        "v": LABEL_VERSION,
    }


def parse_labels(labels: list, valid_ids: set[str], settings: Settings) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for raw in labels or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id", ""))
        if item_id not in valid_ids or item_id in out:
            continue
        label = clean_label(raw, settings)
        if label:
            out[item_id] = label
    return out


@dataclass
class Usage:
    items: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    errors: int = 0


class ClaudeTagger:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        if client is None:
            import anthropic  # imported lazily so offline runs don't need a key
            client = anthropic.Anthropic(max_retries=3, timeout=90)
        self.client = client
        self.system = build_system_prompt(settings)
        self.tool = build_tool(settings)
        self.usage = Usage()

    def _call(self, batch: list[Item]) -> dict[str, dict]:
        payload = [item_payload(i, self.settings) for i in batch]
        resp = self.client.messages.create(
            model=self.settings.model,
            max_tokens=300 + 90 * len(batch),
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            tools=[self.tool],
            tool_choice={"type": "tool", "name": "record_labels"},
            messages=[{
                "role": "user",
                "content": "Label these items:\n" + json.dumps(payload, ensure_ascii=False),
            }],
        )
        self.usage.calls += 1
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.usage.input_tokens += (getattr(usage, "input_tokens", 0) or 0) \
                + (getattr(usage, "cache_creation_input_tokens", 0) or 0) \
                + (getattr(usage, "cache_read_input_tokens", 0) or 0)
            self.usage.output_tokens += getattr(usage, "output_tokens", 0) or 0
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                data = block.input if isinstance(block.input, dict) else {}
                return parse_labels(data.get("labels", []), {i.id for i in batch}, self.settings)
        return {}

    def tag(self, items: list[Item]) -> dict[str, dict]:
        results: dict[str, dict] = {}
        size = self.settings.batch_size
        for start in range(0, len(items), size):
            batch = items[start:start + size]
            try:
                labels = self._call(batch)
            except Exception as exc:  # network trouble, rate limits, bad key...
                self.usage.errors += 1
                log.warning("Labeling batch failed (%s: %s); those items will retry next run.",
                            type(exc).__name__, str(exc)[:200])
                continue
            missing = [i for i in batch if i.id not in labels]
            if missing and len(missing) < len(batch):
                # The model skipped a few. One retry for just those.
                try:
                    labels.update(self._call(missing))
                except Exception as exc:
                    self.usage.errors += 1
                    log.warning("Retry failed (%s); %d items will retry next run.",
                                type(exc).__name__, len(missing))
            results.update(labels)
            self.usage.items += len(labels)
            log.info("Labeled %d/%d items", len(results), len(items))
        return results


class FakeTagger:
    """Keyword stand-in for offline runs and tests. Not smart; just deterministic."""

    NEGATIVE = re.compile(r"\b(dies|dead|death|killed|kill|shooting|crash|war|attack|lawsuit|sued|fraud|"
                          r"scandal|loses|lost|loss|injur\w*|arrest\w*|crisis|fire|flood|threat\w*|fear\w*|"
                          r"collapse\w*|recall|layoffs?|outrage|slams|blasts)\b", re.I)
    UPLIFTING = re.compile(r"\b(rescue\w*|saved?|kind\w*|hero\w*|adopt\w*|cute|puppy|kitten|baby|"
                           r"discover\w*|breakthrough|celebrat\w*|wins?|won|record|first|helps?|"
                           r"donat\w*|recover\w*|reunit\w*|beautiful|stunning)\b", re.I)
    SIGNIFICANT = re.compile(r"\b(supreme court|overturn\w*|national emergency|declares war|pandemic|"
                             r"constitution\w*|election results?)\b", re.I)

    def __init__(self, settings: Settings):
        self.settings = settings
        self.usage = Usage()

    def tag(self, items: list[Item]) -> dict[str, dict]:
        out = {}
        for item in items:
            text = f"{item.title} {item.summary}"
            if self.NEGATIVE.search(text):
                tone = "negative"
            elif self.UPLIFTING.search(text):
                tone = "uplifting"
            else:
                tone = "neutral"
            sig = 5 if self.SIGNIFICANT.search(text) else 2
            flags = []
            if re.search(r"\b(odds|picks|spread|parlay|betting)\b", text, re.I) and "betting_odds" in self.settings.flags:
                flags.append("betting_odds")
            if re.search(r"\b(slams|blasts|destroys|owned)\b", text, re.I) and "outrage_bait" in self.settings.flags:
                flags.append("outrage_bait")
            topic = {"sports": "sports", "pictures": "nature_photography", "science": "science_discovery",
                     "good_news": "kindness_community"}.get(item.category, "other")
            out[item.id] = {"tone": tone, "significance": sig,
                            "topics": [topic if topic in self.settings.topics else "other"],
                            "flags": flags, "neutral_title": "", "v": LABEL_VERSION}
        self.usage.items += len(out)
        return out
