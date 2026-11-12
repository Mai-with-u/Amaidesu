"""自备话题匣：杂谈冷场时从话题卡池里挑出本场没用过的卡，给主播开话头用。

主播在杂谈档（talk）遇到"这一窗没人发弹幕、提纲也没有在聊的要点"（或本环节就是
自由闲聊 / 提问箱环节）时，话题匣按优先级预摆 2 张本场未用的卡进决策参考段
【可抽的话题卡 / 提问箱】；她在 ``streamer_reply`` 里填 ``use_card_id`` 选用某张卡
开话头，该卡即标为本场已用，并交给话头工作台作为当前在聊话头。

真人弹幕永远优先：这一窗有观众弹幕时不预摆卡片，免得她念卡冷落活人。

话题匣是主播 Agent 的内部件（代码直连）；观众点名"再抽一张"时她用 ``talk_draw_cards``
工具主动抽卡，那是同一个话题匣的另一扇门。
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Protocol, Sequence

from src.modules.logging import get_logger
from src.modules.storage.models.talk_card import TalkCard

__all__ = ["DEFAULT_PRESET_COUNT", "TalkCardStore", "TopicDeck", "render_cards"]

# 冷场时预摆几张卡：两张给她挑，多了就成了菜单
DEFAULT_PRESET_COUNT = 2
# 卡种优先级：提问箱是观众真提的问题，先接；其次往场趣事，最后二选一态度
_KIND_PRIORITY = {"marshmallow": 0, "episode": 1, "opinion": 2}
_KIND_LABEL = {"marshmallow": "提问箱", "episode": "往场趣事", "opinion": "二选一"}
# 单张卡渲染时正文截断长度（字）：参考段只需要让她看懂这张卡讲什么
_BODY_PREVIEW_CHARS = 60


class TalkCardStore(Protocol):
    """话题匣依赖的卡池读写面（``TalkCardRepo`` 满足此协议）。"""

    async def list_cards(self, *, kind: Optional[str] = None, enabled_only: bool = False) -> List[TalkCard]: ...

    async def get_card(self, card_id: int) -> Optional[TalkCard]: ...

    async def mark_used(self, card_id: int, *, session_id: Optional[int], at_ms: int) -> bool: ...


def _card_line(card: TalkCard) -> str:
    """一张卡渲染成一行：编号、卡种、标题，带提问人与正文摘要。"""
    head = f"#{card.id}［{_KIND_LABEL.get(card.kind, card.kind)}］{card.title}"
    if card.asker:
        head += f"（{card.asker}问）"
    body = " ".join(card.body.split())
    if body:
        if len(body) > _BODY_PREVIEW_CHARS:
            body = body[: _BODY_PREVIEW_CHARS - 1] + "…"
        head += f"：{body}"
    return head


def render_cards(cards: Sequence[TalkCard]) -> str:
    """把一组卡渲染成参考段文本；空组返回空串。"""
    if not cards:
        return ""
    return "【可抽的话题卡 / 提问箱】\n" + "\n".join(_card_line(c) for c in cards)


class TopicDeck:
    """话题匣：按场挑未用卡、缓存本窗预摆的卡、选用后标已用。

    ``refresh`` 在决策窗开头异步调用（读卡池），``render`` 同步读缓存——Planner
    拼参考段是同步的，不能在那里等数据库。
    """

    def __init__(
        self,
        store: TalkCardStore,
        *,
        session_id_fn: Callable[[], Optional[int]],
        clock: Callable[[], int],
        preset_count: int = DEFAULT_PRESET_COUNT,
    ) -> None:
        self._store = store
        self._session_id_fn = session_id_fn
        self._clock = clock
        self._preset_count = max(1, int(preset_count))
        self._preset: List[TalkCard] = []
        self._logger = get_logger("TopicDeck")

    async def available_cards(self, *, kind: Optional[str] = None, limit: Optional[int] = None) -> List[TalkCard]:
        """本场还没用过的启用卡，按卡种优先级、再按投递先后排序。"""
        session_id = self._session_id_fn()
        cards = await self._store.list_cards(kind=kind, enabled_only=True)
        fresh = [c for c in cards if not c.is_used_in(session_id)]
        fresh.sort(key=lambda c: (_KIND_PRIORITY.get(c.kind, 9), c.id))
        return fresh if limit is None else fresh[: max(0, int(limit))]

    async def refresh(self, *, should_preset: bool) -> List[TalkCard]:
        """决策窗开头刷新预摆卡：该摆时挑出 N 张未用卡缓存起来，不该摆时清空。

        读卡池失败只记日志并清空预摆——没卡可摆不影响她照常说话。
        """
        if not should_preset:
            self._preset = []
            return []
        try:
            self._preset = await self.available_cards(limit=self._preset_count)
        except Exception as exc:
            self._logger.warning("读取话题卡池失败，本窗不预摆话题卡", exc=exc)
            self._preset = []
        return list(self._preset)

    def render(self) -> str:
        """本窗预摆卡的参考段文本（未预摆时为空串）。"""
        return render_cards(self._preset)

    def preset_cards(self) -> List[TalkCard]:
        """本窗预摆卡副本。"""
        return list(self._preset)

    async def use_card(self, card_id: Any) -> Optional[TalkCard]:
        """她选用一张卡开话头：校验卡存在且启用，标为本场已用，返回该卡；无效 id 返回 None。

        选用后从本窗预摆里拿掉，免得同一窗里再被看到。
        """
        try:
            cid = int(card_id)
        except (TypeError, ValueError):
            self._logger.warning(f"use_card_id 不是整数，忽略: {card_id!r}")
            return None
        card = await self._store.get_card(cid)
        if card is None or not card.enabled:
            self._logger.warning(f"选用的话题卡不存在或已停用，忽略: id={cid}")
            return None
        await self._store.mark_used(cid, session_id=self._session_id_fn(), at_ms=self._clock())
        self._preset = [c for c in self._preset if c.id != cid]
        return card
