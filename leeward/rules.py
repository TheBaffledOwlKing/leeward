"""Decide whether an item shows at a given dial level, and say why."""

from __future__ import annotations

from dataclasses import dataclass

from .config import DialLevel, Settings
from .items import Item


@dataclass(frozen=True)
class Decision:
    visible: bool
    reason: str


def decide(item: Item, tags: dict | None, level: DialLevel, settings: Settings) -> Decision:
    category = settings.categories[item.category]

    for pattern in settings.block_keywords:
        if pattern.search(item.title):
            return Decision(False, f"blocked keyword /{pattern.pattern}/")
    for pattern in settings.allow_keywords:
        if pattern.search(item.title):
            return Decision(True, f"allowed keyword /{pattern.pattern}/")

    if tags is None:
        if category.untagged == "show":
            return Decision(True, "not labeled yet (category shows unlabeled items)")
        return Decision(False, "not labeled yet")

    blocked = sorted(set(tags.get("flags", [])) & category.block_flags)
    if blocked:
        return Decision(False, "flagged " + ", ".join(f.replace("_", " ") for f in blocked))

    if category.mode == "pass_all":
        return Decision(True, f"{category.label} shows everything")

    tone = tags.get("tone")
    if tone != "negative":
        return Decision(True, f"{tone}")

    sig = int(tags.get("significance", 0))
    if sig >= settings.emergency_floor:
        return Decision(True, f"negative, but significance {sig} meets the emergency floor")

    threshold = level.negative_min
    priority_hit = sorted(set(tags.get("topics", [])) & settings.priorities)
    if priority_hit and level.priority_min is not None:
        threshold = min(threshold, level.priority_min)

    if sig >= threshold:
        why = f"priority topic {priority_hit[0].replace('_', ' ')}" if priority_hit and threshold < level.negative_min \
            else f"significance {sig}"
        return Decision(True, f"negative, shown for {why} at '{level.name}'")

    if threshold > 5:
        return Decision(False, f"negative, hidden at '{level.name}'")
    return Decision(False, f"negative, significance {sig} is below {threshold} for '{level.name}'")


def display_title(item: Item, tags: dict | None, settings: Settings) -> str:
    if settings.rewrite_titles and tags and tags.get("neutral_title"):
        return tags["neutral_title"]
    return item.title
