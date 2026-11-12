"""主播杂谈与自我表达内部件包（人物设定档案、话头工作台、自备话题匣）。"""

from __future__ import annotations

from src.agents.streamer.talk.deck import DEFAULT_PRESET_COUNT, TalkCardStore, TopicDeck, render_cards
from src.agents.streamer.talk.profile import (
    MAX_PROFILE_CHARS,
    PERSONA_PROFILE_TEMPLATE_MD,
    OutfitEntry,
    PersonaProfile,
    load_persona_profile,
    parse_persona_profile_markdown,
    parse_persona_profile_toml,
    resolve_profile_path,
)
from src.agents.streamer.talk.workbench import MAX_ROUNDS_PER_BEAT, MAX_WORKBENCH_CHARS, TalkWorkbench

__all__ = [
    "DEFAULT_PRESET_COUNT",
    "MAX_ROUNDS_PER_BEAT",
    "MAX_WORKBENCH_CHARS",
    "TalkCardStore",
    "TalkWorkbench",
    "TopicDeck",
    "render_cards",
    "MAX_PROFILE_CHARS",
    "OutfitEntry",
    "PERSONA_PROFILE_TEMPLATE_MD",
    "PersonaProfile",
    "load_persona_profile",
    "parse_persona_profile_markdown",
    "parse_persona_profile_toml",
    "resolve_profile_path",
]
