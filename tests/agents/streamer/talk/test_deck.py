"""自备话题匣：按场挑未用卡、卡种优先级、冷场预摆、选用标已用，以及 talk_draw_cards 工具。"""

from __future__ import annotations

from typing import List, Optional

from src.agents.streamer.talk.deck import TopicDeck, render_cards
from src.agents.streamer.tools.talk_cards_tool import build_talk_cards_tool_provider
from src.modules.storage.models.talk_card import TalkCard
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry


class _MemoryStore:
    """内存卡池替身：行为与 TalkCardRepo 的协议面一致。"""

    def __init__(self, cards: List[TalkCard]) -> None:
        self.cards = {c.id: c for c in cards}
        self.fail = False

    async def list_cards(self, *, kind: Optional[str] = None, enabled_only: bool = False) -> List[TalkCard]:
        if self.fail:
            raise RuntimeError("db down")
        rows = [c for c in self.cards.values() if (kind is None or c.kind == kind)]
        return [c for c in rows if c.enabled or not enabled_only]

    async def get_card(self, card_id: int) -> Optional[TalkCard]:
        return self.cards.get(card_id)

    async def mark_used(self, card_id: int, *, session_id: Optional[int], at_ms: int) -> bool:
        card = self.cards.get(card_id)
        if card is None:
            return False
        self.cards[card_id] = card.model_copy(update={"used_in_session_id": session_id, "used_at_ms": at_ms})
        return True


def _card(card_id: int, kind: str, title: str, **extra: object) -> TalkCard:
    return TalkCard(id=card_id, kind=kind, title=title, created_at_ms=1, updated_at_ms=1, **extra)


def _deck(store: _MemoryStore, session_id: Optional[int] = 3) -> TopicDeck:
    return TopicDeck(store, session_id_fn=lambda: session_id, clock=lambda: 5_000)


def _store() -> _MemoryStore:
    return _MemoryStore(
        [
            _card(1, "opinion", "甜粽子还是咸粽子"),
            _card(2, "episode", "第一次直播卡进河里", body="游了半小时没上岸"),
            _card(3, "marshmallow", "腰上的旋钮能拧吗", asker="观众甲"),
            _card(4, "marshmallow", "停用的问题", enabled=False),
            _card(5, "marshmallow", "上一场用过的问题", used_in_session_id=2, used_at_ms=10),
            _card(6, "marshmallow", "本场用过的问题", used_in_session_id=3, used_at_ms=10),
        ]
    )


async def test_preset_picks_two_unused_cards_by_kind_priority() -> None:
    deck = _deck(_store())
    preset = await deck.refresh(should_preset=True)
    # 提问箱优先（按投递先后），停用卡与本场用过的卡不摆；上一场用过的换场后可用
    assert [c.id for c in preset] == [3, 5]
    text = deck.render()
    assert text.startswith("【可抽的话题卡 / 提问箱】")
    assert "#3［提问箱］腰上的旋钮能拧吗（观众甲问）" in text


async def test_no_preset_when_live_danmaku_or_open_beat() -> None:
    deck = _deck(_store())
    await deck.refresh(should_preset=True)
    assert await deck.refresh(should_preset=False) == []
    assert deck.render() == ""


async def test_store_failure_degrades_to_no_preset() -> None:
    store = _store()
    store.fail = True
    deck = _deck(store)
    assert await deck.refresh(should_preset=True) == []
    assert deck.render() == ""


async def test_use_card_marks_used_and_rejects_invalid_ids() -> None:
    store = _store()
    deck = _deck(store)
    await deck.refresh(should_preset=True)
    used = await deck.use_card("3")
    assert used is not None and used.id == 3
    assert store.cards[3].is_used_in(3) is True
    assert [c.id for c in deck.preset_cards()] == [5]  # 本窗预摆里拿掉已选的

    assert await deck.use_card(4) is None  # 停用卡
    assert await deck.use_card(999) is None
    assert await deck.use_card("abc") is None
    assert [c.id for c in await deck.available_cards()] == [5, 2, 1]


def test_render_cards_truncates_long_body() -> None:
    text = render_cards([_card(9, "episode", "长故事", body="很长" * 50)])
    assert text.endswith("…") and len(text.splitlines()[1]) < 90
    assert render_cards([]) == ""


async def test_draw_cards_tool_lists_without_marking_used() -> None:
    store = _store()
    registry = ToolRegistry()
    registry.register_provider(build_talk_cards_tool_provider(_deck(store)))

    result = await registry.invoke(
        ToolInvocation(tool_name="talk_draw_cards", arguments={"kind": "episode"}, source="planner-react")
    )
    assert result.success is True
    assert [c["id"] for c in result.structured_content["cards"]] == [2]
    assert store.cards[2].used_at_ms is None  # 抽卡只是看看，不算用过

    capped = await registry.invoke(
        ToolInvocation(tool_name="talk_draw_cards", arguments={"limit": 2}, source="planner-react")
    )
    assert [c["id"] for c in capped.structured_content["cards"]] == [3, 5]
    oversized = await registry.invoke(
        ToolInvocation(tool_name="talk_draw_cards", arguments={"limit": 99}, source="planner-react")
    )
    assert len(oversized.structured_content["cards"]) == 4  # 张数夹到上限，池里只剩 4 张可用

    bad = await registry.invoke(
        ToolInvocation(tool_name="talk_draw_cards", arguments={"kind": "gossip"}, source="planner-react")
    )
    assert bad.success is False and "未知卡种" in bad.structured_content["error"]

    store.fail = True
    broken = await registry.invoke(ToolInvocation(tool_name="talk_draw_cards", arguments={}, source="planner-react"))
    assert broken.success is False and "暂时读不出来" in broken.structured_content["error"]
