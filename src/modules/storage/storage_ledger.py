"""
StorageLedger —— 直播间消息流落库记账器

与 EventHistoryRecorder 同类（订阅事件→处理），但写目标不同：
- EventHistoryRecorder → 事件历史服务（dashboard 用）
- StorageLedger       → SQLite 业务表（live chat 行业标准数据平面）

## 职责
- 订阅 ``room.message.#``（MQTT 风格通配），按 payload.message_type 分发：
  - ``danmaku``   → live_chat（+ 顺路 upsert viewers.message_count）
  - ``gift``      → gifts（+ 顺路 upsert viewers.gift_count）
  - ``super_chat`` → super_chats（SC 属 high-value，统计计数走 SimpleMemory 语义层，不混入 viewers）
  - ``enter``     → 当前 schema 无 enter 明细表 → debug 日志后丢弃（场次状态归 LiveSessionManager，不在本层职责）
- viewers 写穿伴随：选在主表落库同点 upsert，避免后台 tick 的重复扫描与时序问题；SC 不计入保持现有行为
- 订阅 ``streamer.speech`` 业务事件（主播发言），写入 live_chat（sender_role="assistant"，message_type="speak"）。
  场次归属取 payload.live_session_id（场次盖章拦截器已注入；0 时回退 LiveSessionManager 解析）。
  若 payload.target_user_id 非空，顺路调用 upsert_viewer_replied 把该观众的
  replied_count/interaction_count +1，形成"主播回复 → 观众被回复计数"闭环。
  payload.reply_to_message_id 落 live_chat.reply_to_message_id 列——"主播回应了
  哪条弹幕"是可查询事实（观众行 message_id ↔ 主播行 reply_to_message_id）。
- 订阅 ``game.*``（milestone / attention_required / error，按 payload.event_type 判别），写入 game_events 表。
  游戏代理（AI 玩家）尚未上线，当前无发布方——写链先行接通，事件出现即落库。
- 端到端贯通 ``simulated`` 字段：payload.simulated → 表列 simulated INTEGER（主播发言/游戏事件天然非模拟，记 False）
- 写入异常降级：单条失败 try/except 记 error 日志，不抛出、不影响主循环（即使记账器挂了，直播流也跑）

## 不做什么
- 不主动建 live_sessions 行（场次行归 LiveSessionManager 创建/结账），这里只
  消费已归属的场次主键写明细。
- 不做统计查询（消费者层 ``WHERE simulated=0``）
- 不改 schema（表结构权威在 schema.py）

## 装配
- 构造时传入 EventBus + ChatRepo/ViewerRepo/EventRepo（按需窄注入） +
  LiveSessionManager，随后调用 ``await ledger.start()``
- ``--dry`` 模式跳过订阅（保留构造便于冒烟，stop 仍可被调）
- run_shutdown 关闭链：放在 EventHistoryRecorder.stop 之后、EventBus.cleanup 之前
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.logging import get_logger
from src.modules.storage.repos import ChatRepo, EventRepo, ViewerRepo

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.session.manager import LiveSessionManager


logger = get_logger("StorageLedger")


# 通配订阅名：覆盖 room.message.danmaku/gift/super_chat/enter 四类（常量定义见 CoreEvents）
_ROOM_MESSAGE_WILDCARD = CoreEvents.ROOM_MESSAGE_WILDCARD
# 通配订阅名：覆盖 game.milestone / game.attention_required / game.error（单层）
_GAME_EVENT_WILDCARD = "game.*"


class StorageLedger:
    """直播间行为流落库记账器。

    独立的 EventBus 订阅者；写目标为明细三表（ChatRepo）、观众统计
    （ViewerRepo）与 game_events（EventRepo）。与 EventHistoryRecorder
    并存——后者写事件历史，前者写业务明细——不冲突。
    """

    def __init__(
        self,
        event_bus: "EventBus",
        chat_repo: ChatRepo,
        viewer_repo: ViewerRepo,
        event_repo: EventRepo,
        *,
        session_manager: Optional["LiveSessionManager"] = None,
    ) -> None:
        self.event_bus = event_bus
        self.chat_repo = chat_repo
        self.viewer_repo = viewer_repo
        self.event_repo = event_repo
        # 场次归属解析（payload 未盖章时回退）；None 时按无场次降级跳过
        self._session_manager = session_manager
        # 订阅句柄表（stop 时按 event_name 取消）
        self._subscriptions: dict[str, Callable] = {}
        self._started = False

    async def start(self) -> None:
        """订阅 ``room.message.#`` 并按 payload.message_type 分发写入。"""
        if self._started:
            logger.debug("StorageLedger 已启动，跳过重复订阅")
            return
        handler = self._on_room_message
        self.event_bus.on(
            _ROOM_MESSAGE_WILDCARD,
            handler,
            model_class=RoomMessagePayload,
        )
        self._subscriptions[_ROOM_MESSAGE_WILDCARD] = handler

        speech_handler = self._on_streamer_speech
        self.event_bus.on(
            CoreEvents.STREAMER_SPEECH,
            speech_handler,
            model_class=StreamerSpeechPayload,
        )
        self._subscriptions[CoreEvents.STREAMER_SPEECH] = speech_handler

        game_handler = self._on_game_event
        self.event_bus.on(
            _GAME_EVENT_WILDCARD,
            game_handler,
            model_class=GamePayload,
        )
        self._subscriptions[_GAME_EVENT_WILDCARD] = game_handler

        self._started = True
        logger.info(
            f"StorageLedger 已订阅 {_ROOM_MESSAGE_WILDCARD}"
            "（danmaku→live_chat / gift→gifts / super_chat→super_chats / enter→debug 丢弃）"
            f" + {CoreEvents.STREAMER_SPEECH}（→live_chat, sender_role=assistant）"
            f" + {_GAME_EVENT_WILDCARD}（milestone/attention_required/error→game_events）",
        )

    async def stop(self) -> None:
        """取消订阅。"""
        for event_name, handler in list(self._subscriptions.items()):
            try:
                self.event_bus.off(event_name, handler)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"取消订阅 {event_name} 失败: {exc}")
        self._subscriptions.clear()
        self._started = False
        logger.info("StorageLedger 已停止")

    # -------------------- 分发：payload → 表 --------------------

    async def _on_room_message(
        self,
        event_name: str,
        payload: RoomMessagePayload,
        source: str,
    ) -> None:
        """room.message.* 通配回调：分发到对应明细表写入。

        异常隔离：单条失败仅记 error 日志，不抛出（即使记账器挂了，主直播流仍跑）。
        无显式场次期间 ``live_pk`` 为 None，所有消息仅在内存流转、不落库。
        """
        try:
            msg_type = payload.message_type
            live_pk = await self._resolve_live_pk(payload.live_session_id)
            if live_pk is None:
                logger.debug(
                    f"{_ROOM_MESSAGE_WILDCARD} 事件无法归属场次（未开启显式场次），跳过落库（message_type={msg_type}）"
                )
                return
            if msg_type == "danmaku":
                await self.chat_repo.insert_live_chat(
                    live_session_id=live_pk,
                    timestamp_ms=payload.timestamp_ms,
                    sender_role="viewer",
                    sender_id=payload.user.id,
                    sender_name=payload.user.name,
                    content=payload.content or "",
                    message_type=msg_type,
                    message_id=payload.message_id or None,
                    simulated=payload.simulated,
                )
                await self.viewer_repo.upsert_viewer_message(
                    user_id=payload.user.id,
                    user_name=payload.user.name,
                    timestamp_ms=payload.timestamp_ms,
                )
                return
            if msg_type == "gift":
                gift = payload.gift
                if gift is None:
                    logger.debug("gift 事件 payload.gift 为空，跳过（采集器层补字段）")
                    return
                await self.chat_repo.insert_gift(
                    live_session_id=live_pk,
                    timestamp_ms=payload.timestamp_ms,
                    user_id=payload.user.id,
                    user_name=payload.user.name,
                    gift_name=gift.name,
                    gift_count=int(gift.count),
                    simulated=payload.simulated,
                )
                await self.viewer_repo.upsert_viewer_gift(
                    user_id=payload.user.id,
                    user_name=payload.user.name,
                    timestamp_ms=payload.timestamp_ms,
                )
                return
            if msg_type == "super_chat":
                sc = payload.sc
                if sc is None:
                    logger.debug("super_chat 事件 payload.sc 为空，跳过")
                    return
                await self.chat_repo.insert_super_chat(
                    live_session_id=live_pk,
                    timestamp_ms=payload.timestamp_ms,
                    user_id=payload.user.id,
                    user_name=payload.user.name,
                    amount=float(sc.amount),
                    message=payload.content or "",
                    simulated=payload.simulated,
                )
                return
            if msg_type == "enter":
                # 当前 schema 无 enter 明细表（live_sessions 心跳与 enter 概念不同，
                # live_sessions 维护由未来的 session_manager 负责）；本层不持有该职责，
                # 这里只留 debug 日志以便后续接入时定位流量。
                logger.debug(f"enter 事件未落库（schema 无 enter 表；live_session_id={payload.live_session_id}）")
                return
            logger.warning(f"未知 message_type={msg_type!r}（event={event_name}），跳过")
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志
            logger.error(
                f"StorageLedger 写入失败（event={event_name}, message_type={getattr(payload, 'message_type', '?')}, "
                f"simulated={getattr(payload, 'simulated', '?')}): {exc}",
                exc_info=True,
            )

    async def _on_streamer_speech(
        self,
        event_name: str,
        payload: StreamerSpeechPayload,
        source: str,
    ) -> None:
        """``streamer.speech`` 回调：主播发言写入 live_chat（sender_role=assistant）。

        场次归属：payload.live_session_id 已由场次盖章拦截器注入；为 0 时回退
        ``LiveSessionManager.resolve_pk()``；两者皆不可用时记 debug 跳过（与
        现有"单条失败/缺关键字段降级"风格一致）。

        ``payload.reply_to_message_id`` 落 live_chat.reply_to_message_id 列，
        与观众行的 message_id 构成"回复了哪条弹幕"的可查询关联。

        若 ``payload.target_user_id`` 非空，在主表 insert 之后顺路调用
        ``upsert_viewer_replied``，把对应观众的 ``replied_count`` /
        ``interaction_count`` +1，形成"主播回复 → 观众被回复计数"的闭环。

        异常隔离：单条失败仅记 error 日志，不抛出（即使记账器挂了，主直播流仍跑）。
        """
        try:
            live_pk = await self._resolve_live_pk(payload.live_session_id)
            if live_pk is None:
                logger.debug(
                    f"{CoreEvents.STREAMER_SPEECH} 事件无法归属场次（未开启显式场次或未盖章），"
                    f"跳过落库（utterance_id={payload.utterance_id}）",
                )
                return
            await self.chat_repo.insert_live_chat(
                live_session_id=live_pk,
                timestamp_ms=payload.timestamp_ms,
                sender_role="assistant",
                sender_name="主播",
                content=payload.text,
                message_type="speak",
                reply_to_message_id=payload.reply_to_message_id or None,
                simulated=False,
            )
            if payload.target_user_id:
                await self.viewer_repo.upsert_viewer_replied(
                    user_id=payload.target_user_id,
                    timestamp_ms=payload.timestamp_ms,
                )
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志
            logger.error(
                f"StorageLedger 写入主播发言失败（event={event_name}, "
                f"utterance_id={getattr(payload, 'utterance_id', '?')}）：{exc}",
                exc_info=True,
            )

    async def _on_game_event(
        self,
        event_name: str,
        payload: GamePayload,
        source: str,
    ) -> None:
        """``game.*`` 通配回调：游戏里程碑/安全阀/异常写入 game_events 表。

        场次归属与其他域一致：消费 payload 上由场次盖章拦截器注入的
        ``live_session_id``（int 主键）；未归属时经 ``_resolve_live_pk``
        回退会话管理器，仍无场次则跳过。写入异常隔离：单条失败仅记
        error 日志，不抛出。
        """
        try:
            live_pk = await self._resolve_live_pk(payload.live_session_id)
            if live_pk is None:
                logger.debug(f"game.* 事件无法归属场次，跳过落库（event_type={payload.event_type}）")
                return
            await self.event_repo.insert_game_event(
                live_session_id=live_pk,
                game=payload.game,
                event_type=payload.event_type,
                message=payload.message,
                scene=payload.scene or None,
                timestamp_ms=payload.timestamp_ms,
            )
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志
            logger.error(
                f"StorageLedger 写入游戏事件失败（event={event_name}, "
                f"event_type={getattr(payload, 'event_type', '?')}）：{exc}",
                exc_info=True,
            )

    # -------------------- 场次归属解析 --------------------

    async def _resolve_live_pk(self, stamped_pk: int = 0) -> Optional[int]:
        """解析明细行的场次主键。

        优先消费 payload 上已盖章的 ``live_session_id``（>0）；为 0 时回退
        ``LiveSessionManager.resolve_pk()``；管理器不可用返回 None（调用方降级）。
        """
        if stamped_pk and stamped_pk > 0:
            return stamped_pk
        if self._session_manager is None:
            return None
        try:
            return await self._session_manager.resolve_pk()
        except Exception as exc:  # noqa: BLE001 归属失败降级，不阻断主直播流
            logger.warning(f"场次归属解析失败，明细行跳过落库: {exc}")
            return None


__all__ = ["StorageLedger"]
