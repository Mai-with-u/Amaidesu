"""游戏里的人落库器：公屏聊天、她在公屏说的话、玩家互动 → 玩家表、互动表、公屏聊天表。

订阅三条只落库的游戏事件，按游戏给的 UUID 认人：

- ``game.chat.received``：只记公屏上玩家说的话。私聊只给她一个人看，不进直播、不进记忆，不落库；
  系统消息不是谁说的话，不落库。带 UUID、游戏核对过此刻在线的发言人才建玩家、挂上他那句；
  没有 UUID 或没核对过的（控制台、命令方块借名发的，没签名服务器转来的整行）只占一行位置、
  不存原话、不建玩家——判断"她这句之前有没有别人插话"要知道有人说过话。
- ``game.self.chatted``：她在公屏打出去的一句，记成"她说的"。
- ``game.player.interacted``：打招呼（同一位玩家一场合并计数）、送东西、切磋。只有名字没有 UUID 的
  （切磋按名字点的对手）按"本场见过的、叫这个名字的只有一位"去对，对不上不记。

没归属到场次的事件（没开播时）不落库：玩家记忆只记直播里她亲身经历、画面里看得到的事。
UUID 在不记录名单里的玩家（被运营者删过、或连同观众一起删过）再出现也不记：他的话只占一行位置。
玩家在公屏发"忘了我"、撤回短语，和观众同一套口令与宽限期，由隐私服务受理；命令那一句本身不存原话。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload, GameSelfChattedPayload
from src.modules.events.payloads.game_player import GamePlayerInteractedPayload
from src.modules.logging import get_logger
from src.modules.storage.models.game_players import (
    GAME_CHAT_OTHER,
    GAME_CHAT_PLAYER,
    GAME_CHAT_STREAMER,
    INTERACTION_GREET,
)
from src.modules.storage.repos.game_players import GamePlayerRepo

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.storage.privacy import PrivacyService

logger = get_logger("GamePlayerLedger")


class GamePlayerLedger:
    """游戏里的人落库器（订阅公屏聊天、她的公屏说话与玩家互动三条事件）。"""

    def __init__(
        self, event_bus: "EventBus", player_repo: GamePlayerRepo, privacy: Optional["PrivacyService"] = None
    ) -> None:
        """``privacy`` 缺失时不查不记录名单（极简装配与部分测试）。"""
        self.event_bus = event_bus
        self.player_repo = player_repo
        self._privacy = privacy
        self._subscriptions: Dict[str, Callable] = {}

    async def start(self) -> None:
        """订阅三条事件；重复调用不重复订阅。"""
        if self._subscriptions:
            return
        for event_name, handler, model in (
            (CoreEvents.GAME_CHAT_RECEIVED, self._on_game_chat, GameChatPayload),
            (CoreEvents.GAME_SELF_CHATTED, self._on_self_chatted, GameSelfChattedPayload),
            (CoreEvents.GAME_PLAYER_INTERACTED, self._on_interacted, GamePlayerInteractedPayload),
        ):
            self.event_bus.on(event_name, handler, model_class=model)
            self._subscriptions[event_name] = handler
        logger.info(
            f"GamePlayerLedger 已订阅 {CoreEvents.GAME_CHAT_RECEIVED} + {CoreEvents.GAME_SELF_CHATTED}"
            f" + {CoreEvents.GAME_PLAYER_INTERACTED}"
        )

    async def stop(self) -> None:
        """取消订阅。"""
        for event_name, handler in list(self._subscriptions.items()):
            try:
                self.event_bus.off(event_name, handler)
            except Exception as exc:  # noqa: BLE001 - 退订失败只影响关闭顺序，记下继续
                logger.warning(f"取消订阅 {event_name} 失败: {exc}", exc=exc)
        self._subscriptions.clear()

    async def _on_game_chat(self, event_name: str, payload: GameChatPayload, source: str) -> None:
        """公屏上玩家说的话：核对过的玩家建档并挂上他那句，认不出是谁的只占位；私聊、系统消息不落。"""
        del event_name, source
        if payload.live_session_id <= 0 or payload.kind != "player" or payload.private:
            return
        try:
            checked = bool(payload.sender_id and payload.sender_online)
            # 核对过的玩家先过隐私口令：发"忘了我"、已在名单里的都不再记，命令那句也只占位
            if checked and await self._player_chat_disposition(payload) == "normal":
                player = await self.player_repo.meet_player(
                    game=payload.game,
                    uuid=payload.sender_id,
                    name=payload.sender,
                    live_session_id=payload.live_session_id,
                    save_id=payload.save_id,
                    save_name=payload.save_name,
                    at_ms=payload.timestamp_ms,
                )
                await self.player_repo.add_game_chat(
                    live_session_id=payload.live_session_id,
                    game=payload.game,
                    direction=GAME_CHAT_PLAYER,
                    content=payload.content,
                    player_id=player.id,
                    save_id=payload.save_id,
                    save_name=payload.save_name,
                    at_ms=payload.timestamp_ms,
                )
                return
            # 认不出是谁说的、或者是不再记的玩家：只占一行位置，不存原话、不建玩家
            await self.player_repo.add_game_chat(
                live_session_id=payload.live_session_id,
                game=payload.game,
                direction=GAME_CHAT_OTHER,
                content="",
                save_id=payload.save_id,
                save_name=payload.save_name,
                at_ms=payload.timestamp_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 落库失败不挡聊天流
            logger.error(f"公屏聊天落库失败（场次 {payload.live_session_id}）: {exc}", exc=exc)

    async def _on_self_chatted(self, event_name: str, payload: GameSelfChattedPayload, source: str) -> None:
        """她在公屏打出去的一句：记成"她说的"，落库前和台词一样遮掉不记录名单里的名字。"""
        del event_name, source
        if payload.live_session_id <= 0:
            return
        try:
            # 她点了发过"忘了我"、或被删过的人的名字：和台词同一个遮名集合，观众换成"一位观众"、玩家换成"一位玩家"
            content = self._privacy.mask_speech(payload.message) if self._privacy is not None else payload.message
            await self.player_repo.add_game_chat(
                live_session_id=payload.live_session_id,
                game=payload.game,
                direction=GAME_CHAT_STREAMER,
                content=content,
                save_id=payload.save_id,
                save_name=payload.save_name,
                at_ms=payload.timestamp_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 落库失败不挡游戏 Agent
            logger.error(f"她的公屏说话落库失败（场次 {payload.live_session_id}）: {exc}", exc=exc)

    async def _on_interacted(self, event_name: str, payload: GamePlayerInteractedPayload, source: str) -> None:
        """一次互动：按 UUID 建档或更新；只有名字的按本场见过的同名玩家去对，对不上不记。"""
        del event_name, source
        if payload.live_session_id <= 0:
            return
        try:
            player_id = await self._player_of(payload)
            if player_id is None:
                logger.debug(f"互动对不上玩家，不记（{payload.kind}，名字 {payload.player_name!r}）")
                return
            if payload.kind == INTERACTION_GREET:
                # 一条事件算一次招呼，这一段里做过的每种动作各记一次
                await self.player_repo.record_greeting(
                    player_id=player_id,
                    live_session_id=payload.live_session_id,
                    actions=payload.actions,
                    save_id=payload.save_id,
                    save_name=payload.save_name,
                    at_ms=payload.timestamp_ms,
                )
                return
            await self.player_repo.add_interaction(
                player_id=player_id,
                live_session_id=payload.live_session_id,
                kind=payload.kind,
                fact=payload.fact,
                save_id=payload.save_id,
                save_name=payload.save_name,
                at_ms=payload.timestamp_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 落库失败不挡游戏事件流
            logger.error(f"玩家互动落库失败（场次 {payload.live_session_id}，{payload.kind}）: {exc}", exc=exc)

    async def _player_of(self, payload: GamePlayerInteractedPayload) -> Optional[int]:
        """互动对应的玩家主键：有 UUID 就建档或更新；只有名字的找本场见过的唯一同名玩家。"""
        if payload.player_uuid:
            if await self._forgotten(payload.game, payload.player_uuid):
                return None
            player = await self.player_repo.meet_player(
                game=payload.game,
                uuid=payload.player_uuid,
                name=payload.player_name,
                live_session_id=payload.live_session_id,
                save_id=payload.save_id,
                save_name=payload.save_name,
                at_ms=payload.timestamp_ms,
            )
            return player.id
        if not payload.player_name:
            return None
        return await self.player_repo.session_player_named(
            game=payload.game, name=payload.player_name, live_session_id=payload.live_session_id
        )

    async def _player_chat_disposition(self, payload: GameChatPayload) -> str:
        """一位核对过的玩家这句话的隐私处理结果；查不了时按正常处理（只少一道拦截，落库照常）。"""
        if self._privacy is None:
            return "normal"
        try:
            return await self._privacy.process_incoming_player_chat(
                game=payload.game,
                uuid=payload.sender_id,
                name=payload.sender,
                content=payload.content,
                at_ms=payload.timestamp_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 名单查不了不挡落库
            logger.warning(f"玩家公屏消息的隐私处理失败，按正常处理: {exc}", exc=exc)
            return "normal"

    async def _forgotten(self, game: str, uuid: str) -> bool:
        """这位玩家是否在不记录名单里；查不了时按没在处理（只是少一道拦截，落库照常）。"""
        if self._privacy is None:
            return False
        try:
            return await self._privacy.is_player_forgotten(game=game, uuid=uuid)
        except Exception as exc:  # noqa: BLE001 - 名单查不了不挡落库
            logger.warning(f"查玩家是否在不记录名单失败，按没在处理: {exc}", exc=exc)
            return False


__all__ = ["GamePlayerLedger"]
