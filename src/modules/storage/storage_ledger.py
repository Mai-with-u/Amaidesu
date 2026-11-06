"""
StorageLedger —— 直播间消息流落库器

与 EventHistoryRecorder 同类（订阅事件→处理），但写目标不同：
- EventHistoryRecorder → 事件历史服务（dashboard 用）
- StorageLedger       → SQLite 业务表（live chat 行业标准数据平面）

## 职责
- 订阅 ``room.message.#``（MQTT 风格通配），按 payload.message_type 分发。
  观众四类消息（弹幕/礼物/SC/上舰）在判场次之前先经 ``PrivacyService`` 处理遗忘命令、撤回短语与不记录名单：
  - 非模拟弹幕命中遗忘命令 → 立即进不记录名单、记宽限删除请求、遮名，本条弹幕不落库（开播前后同样生效）
  - 非模拟弹幕命中撤回短语且有待执行删除 → 移出名单、清删除请求，本条弹幕照常落库
  - 命中不记录名单 → 不建观众、不计统计；弹幕与上舰文本行不落库，礼物/SC/上舰购买表只存金额、类型、时刻
  - 其余消息照常归属场次、解析观众身份并落库（正式场首条真弹幕同步计入 ``viewers.sessions_count``）
- 订阅 ``streamer.speech`` 业务事件（主播发言），先将本场遮名集合中的昵称替换为"一位观众"，
  携带 ``utterance_id`` 与 ``memory_refs`` 写入 ``live_chat``（sender_role="assistant"，message_type="speak"）。
- 订阅 ``tts.utterance.dropped``（播放队列确知未送达观众面前的句子），按 ``utterance_id`` 写 ``dropped_at_ms``；
  未命中台词行时记告警并统计本场缺失次数，在下播日志输出供观测。
- 订阅 ``live.ended``：输出本场没送达标记未命中次数、清空本场内存遮名集合，并在未交由主播下播整理托管时触发到期清理。
- 订阅 ``game.*``（milestone / attention_required / error），写入 game_events 表。
- 订阅 ``game.place.noted``（记住、忘掉、走近、离开一个记住的地点），写成 game_events 里 ``place_*`` 类型的行，
  只给下播整理认地点用。
- 写入异常降级：单条失败 try/except 记 error 日志，不抛出、不影响主循环。
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from src.modules.config.storage_schemas import ViewerIdentityConfig
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_place import GamePlacePayload
from src.modules.events.payloads.live import LiveEndedPayload
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import UtteranceDroppedPayload
from src.modules.logging import get_logger
from src.modules.storage.privacy import PrivacyService
from src.modules.storage.repos import ChatRepo, EventRepo, ViewerRepo
from src.modules.storage.retention import RetentionService

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.session.manager import LiveSessionManager


logger = get_logger("StorageLedger")


_ROOM_MESSAGE_WILDCARD = CoreEvents.ROOM_MESSAGE_WILDCARD
_GAME_EVENT_WILDCARD = "game.*"
_VIEWER_MESSAGE_TYPES = frozenset({"danmaku", "gift", "super_chat", "guard"})
_DAY_MS = 86_400_000


class StorageLedger:
    """直播间行为流落库器。"""

    def __init__(
        self,
        event_bus: "EventBus",
        chat_repo: ChatRepo,
        viewer_repo: ViewerRepo,
        event_repo: EventRepo,
        *,
        session_manager: Optional["LiveSessionManager"] = None,
        name_match_window_ms: Optional[int] = None,
        privacy: Optional[PrivacyService] = None,
        retention: Optional[RetentionService] = None,
        cleanup_on_live_ended: bool = True,
    ) -> None:
        self.event_bus = event_bus
        self.chat_repo = chat_repo
        self.viewer_repo = viewer_repo
        self.event_repo = event_repo
        self._session_manager = session_manager
        self._name_match_window_ms = (
            name_match_window_ms
            if name_match_window_ms is not None
            else ViewerIdentityConfig().name_match_window_days * _DAY_MS
        )
        self._privacy = privacy
        self._retention = retention
        self._cleanup_on_live_ended = cleanup_on_live_ended
        self._dropped_miss_count = 0
        self._subscriptions: dict[str, Callable] = {}
        self._started = False
        # 在写的用户可见文本处理数：主播 Agent 的下播收尾要等最后几句台词与观众消息
        # 落库之后再清遮名、执行删除（见 wait_writes_idle）。事件回调都在同一个事件
        # 循环里跑，计数不需要加锁
        self._writes_in_flight = 0
        self._writes_idle = asyncio.Event()
        self._writes_idle.set()

    def set_cleanup_on_live_ended(self, enabled: bool) -> None:
        """由主播 Agent 接管下播清理时置 False。

        接管后本类不在 ``live.ended`` 里清遮名、也不跑到期清理——主播 Agent
        还要等道别那一轮落库、队尾台词播完、本场整理跑完，才调 ``finish_stream_end``；
        没人接管（没有主播 Agent，或它在启动时就已停止）时保持原行为。
        """
        self._cleanup_on_live_ended = enabled

    async def wait_writes_idle(self, *, timeout_ms: int = 15_000) -> bool:
        """等当前在写的观众消息与主播台词落库；全部写完返回 True，等满上限返回 False。

        下播收尾用的时序口：清遮名与执行删除必须排在最后几句台词写库之后，否则
        道别句里的观众昵称会漏出、整理也会读到已删的观众。只等"已经在写"的处理，
        不拦之后新到的事件（那时场次已经结账，事件归属不到场次、不会再写）。
        """
        if self._writes_in_flight == 0:
            return True
        try:
            await asyncio.wait_for(self._writes_idle.wait(), timeout=max(1, int(timeout_ms)) / 1000)
        except asyncio.TimeoutError:
            logger.warning(
                f"等下播写库落完超时（{timeout_ms} 毫秒，仍有 {self._writes_in_flight} 个处理在写），照常继续清理"
            )
            return False
        return True

    async def finish_stream_end(self, *, clear_masks: bool = True) -> None:
        """下播收尾：先执行保留期与到期待删观众的清理，最后清空本场遮名集合。

        由主播 Agent 在道别、队尾与下播整理都结束后调用（遮名要留到最后一句台词写库）；
        没有主播 Agent 接管时由 ``_on_live_ended`` 自己调。``clear_masks=False`` 用于
        道别期间已经开了新场次的情形：旧场次照样跑清理，但新场次刚记下的遮名不能被抹掉。
        """
        if self._retention is not None:
            await self._retention.run_cleanup(after_stream_end=True)
        if self._privacy is not None and clear_masks:
            self._privacy.clear_session_masks()

    def _tracked(self, handler: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
        """把写库回调包一层在写计数：收尾等库时能知道还有没有处理在写。"""

        async def _wrapped(event_name: str, payload: Any, source: str) -> None:
            self._writes_in_flight += 1
            self._writes_idle.clear()
            try:
                await handler(event_name, payload, source)
            finally:
                self._writes_in_flight = max(0, self._writes_in_flight - 1)
                if self._writes_in_flight == 0:
                    self._writes_idle.set()

        return _wrapped

    async def start(self) -> None:
        """订阅行为流、发言、丢弃、游戏与下播事件。"""
        if self._started:
            logger.debug("StorageLedger 已启动，跳过重复订阅")
            return
        handler = self._tracked(self._on_room_message)
        self.event_bus.on(
            _ROOM_MESSAGE_WILDCARD,
            handler,
            model_class=RoomMessagePayload,
        )
        self._subscriptions[_ROOM_MESSAGE_WILDCARD] = handler

        speech_handler = self._tracked(self._on_streamer_speech)
        self.event_bus.on(
            CoreEvents.STREAMER_SPEECH,
            speech_handler,
            model_class=StreamerSpeechPayload,
        )
        self._subscriptions[CoreEvents.STREAMER_SPEECH] = speech_handler

        dropped_handler = self._on_utterance_dropped
        self.event_bus.on(
            CoreEvents.TTS_UTTERANCE_DROPPED,
            dropped_handler,
            model_class=UtteranceDroppedPayload,
        )
        self._subscriptions[CoreEvents.TTS_UTTERANCE_DROPPED] = dropped_handler

        game_handler = self._on_game_event
        self.event_bus.on(
            _GAME_EVENT_WILDCARD,
            game_handler,
            model_class=GamePayload,
        )
        self._subscriptions[_GAME_EVENT_WILDCARD] = game_handler

        # 地点动静是三层名，单层 game.* 接不住：显式订阅，落成 place_* 类型的游戏事件行
        place_handler = self._on_place_noted
        self.event_bus.on(
            CoreEvents.GAME_PLACE_NOTED,
            place_handler,
            model_class=GamePlacePayload,
        )
        self._subscriptions[CoreEvents.GAME_PLACE_NOTED] = place_handler

        ended_handler = self._on_live_ended
        self.event_bus.on(
            CoreEvents.LIVE_ENDED,
            ended_handler,
            model_class=LiveEndedPayload,
        )
        self._subscriptions[CoreEvents.LIVE_ENDED] = ended_handler

        self._started = True
        logger.info(
            f"StorageLedger 已订阅 {_ROOM_MESSAGE_WILDCARD}"
            f" + {CoreEvents.STREAMER_SPEECH}"
            f" + {CoreEvents.TTS_UTTERANCE_DROPPED}"
            f" + {_GAME_EVENT_WILDCARD}"
            f" + {CoreEvents.GAME_PLACE_NOTED}"
            f" + {CoreEvents.LIVE_ENDED}"
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

    _SILVER_CURRENCIES = frozenset({"bilibili_silver_coin"})

    @staticmethod
    def _is_paid_currency(currency: str) -> bool:
        """币种是否计付费：银瓜子（免费礼物）与空币种（调试数据）不计。"""
        return bool(currency) and currency not in StorageLedger._SILVER_CURRENCIES

    async def _on_room_message(
        self,
        event_name: str,
        payload: RoomMessagePayload,
        source: str,
    ) -> None:
        """room.message.* 通配回调：先过隐私入口，再归属场次写入对应明细表。"""
        try:
            msg_type = payload.message_type

            # 隐私入口排在判场次之前：开播前、下播后发的"忘了我"与撤回短语同样立即生效
            if msg_type in _VIEWER_MESSAGE_TYPES and self._privacy is not None:
                disposition = await self._privacy.process_incoming_viewer_message(
                    platform=payload.platform,
                    id_kind=payload.user.id_kind,
                    external_id=payload.user.id,
                    open_app_id=payload.user.open_app_id,
                    user_name=payload.user.name,
                    message_type=msg_type,
                    content=payload.content or "",
                    message_id=payload.message_id or None,
                    simulated=payload.simulated,
                    timestamp_ms=payload.timestamp_ms,
                )
                if disposition == "forget_command":
                    return
                if disposition == "forget_listed":
                    await self._persist_forgotten_viewer_payment(payload)
                    return

            live_pk = await self._resolve_live_pk(payload.live_session_id)
            if live_pk is None:
                logger.debug(
                    f"{_ROOM_MESSAGE_WILDCARD} 事件无法归属场次（未开启显式场次），跳过落库（message_type={msg_type}）"
                )
                return
            viewer_id = await self._resolve_viewer(payload) if msg_type in _VIEWER_MESSAGE_TYPES else None
            id_kind = payload.user.id_kind if msg_type in _VIEWER_MESSAGE_TYPES else ""
            if msg_type == "danmaku":
                await self.chat_repo.insert_live_chat(
                    live_session_id=live_pk,
                    timestamp_ms=payload.timestamp_ms,
                    platform=payload.platform,
                    sender_role="viewer",
                    sender_id=payload.user.id,
                    sender_name=payload.user.name,
                    content=payload.content or "",
                    message_type=msg_type,
                    message_id=payload.message_id or None,
                    simulated=payload.simulated,
                    viewer_id=viewer_id,
                    id_kind=id_kind,
                )
                if viewer_id is not None:
                    await self.viewer_repo.record_message(
                        viewer_id=viewer_id,
                        timestamp_ms=payload.timestamp_ms,
                        live_session_id=live_pk,
                        simulated=payload.simulated,
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
                    platform=payload.platform,
                    user_id=payload.user.id,
                    user_name=payload.user.name,
                    gift_name=gift.name,
                    quantity=int(gift.count),
                    gift_id=gift.gift_id,
                    unit_price=gift.unit_price,
                    total_price=gift.total_price,
                    paid_price=gift.paid_price,
                    currency=gift.currency,
                    guard_level=gift.guard_level,
                    fans_medal_level=gift.fans_medal_level,
                    fans_medal_name=gift.fans_medal_name,
                    combo_id=gift.combo_id,
                    combo_count=gift.combo_count,
                    combo_gift=gift.combo_gift,
                    blind_gift_id=gift.blind_gift_id,
                    msg_id=gift.msg_id,
                    raw_data=gift.raw_data or None,
                    simulated=payload.simulated,
                    viewer_id=viewer_id,
                    id_kind=id_kind,
                )
                if viewer_id is None:
                    return
                await self.viewer_repo.record_gift(viewer_id=viewer_id, timestamp_ms=payload.timestamp_ms)
                if self._is_paid_currency(gift.currency):
                    await self.viewer_repo.record_paid(
                        viewer_id=viewer_id, amount=gift.total_price, timestamp_ms=payload.timestamp_ms
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
                    platform=payload.platform,
                    user_id=payload.user.id,
                    user_name=payload.user.name,
                    message=payload.content or "",
                    total_price=sc.total_price,
                    currency=sc.currency,
                    start_time=sc.start_time,
                    end_time=sc.end_time,
                    guard_level=sc.guard_level,
                    fans_medal_level=sc.fans_medal_level,
                    fans_medal_name=sc.fans_medal_name,
                    message_id=sc.message_id,
                    raw_data=sc.raw_data or None,
                    simulated=payload.simulated,
                    viewer_id=viewer_id,
                    id_kind=id_kind,
                )
                if viewer_id is not None and self._is_paid_currency(sc.currency):
                    await self.viewer_repo.record_paid(
                        viewer_id=viewer_id, amount=sc.total_price, timestamp_ms=payload.timestamp_ms
                    )
                return
            if msg_type == "guard":
                guard = payload.guard
                if guard is not None:
                    await self.chat_repo.insert_guard(
                        live_session_id=live_pk,
                        timestamp_ms=payload.timestamp_ms,
                        platform=payload.platform,
                        user_id=payload.user.id,
                        user_name=payload.user.name,
                        guard_level=guard.guard_level,
                        guard_num=guard.guard_num,
                        guard_unit=guard.guard_unit,
                        total_price=guard.total_price,
                        currency=guard.currency,
                        fans_medal_level=guard.fans_medal_level,
                        fans_medal_name=guard.fans_medal_name,
                        msg_id=guard.msg_id,
                        raw_data=guard.raw_data or None,
                        simulated=payload.simulated,
                        viewer_id=viewer_id,
                        id_kind=id_kind,
                    )
                    if viewer_id is not None and self._is_paid_currency(guard.currency):
                        await self.viewer_repo.record_paid(
                            viewer_id=viewer_id, amount=guard.total_price, timestamp_ms=payload.timestamp_ms
                        )
                else:
                    logger.debug("guard 事件 payload.guard 为空，仅落 live_chat 文本行")
                await self.chat_repo.insert_live_chat(
                    live_session_id=live_pk,
                    timestamp_ms=payload.timestamp_ms,
                    platform=payload.platform,
                    sender_role="viewer",
                    sender_id=payload.user.id,
                    sender_name=payload.user.name,
                    content=payload.content or "",
                    message_type=msg_type,
                    message_id=payload.message_id or None,
                    simulated=payload.simulated,
                    viewer_id=viewer_id,
                    id_kind=id_kind,
                )
                return
            if msg_type == "partner_speech":
                await self.chat_repo.insert_live_chat(
                    live_session_id=live_pk,
                    timestamp_ms=payload.timestamp_ms,
                    platform=payload.platform,
                    sender_role="partner",
                    sender_id=payload.user.id,
                    sender_name=payload.user.name,
                    content=payload.content or "",
                    message_type=msg_type,
                    message_id=payload.message_id or None,
                    simulated=payload.simulated,
                )
                return
            if msg_type == "enter":
                logger.debug(f"enter 事件未落库（schema 无 enter 表；live_session_id={payload.live_session_id}）")
                return
            logger.warning(f"未知 message_type={msg_type!r}（event={event_name}），跳过")
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志
            logger.error(
                f"StorageLedger 写入失败（event={event_name}, message_type={getattr(payload, 'message_type', '?')}, "
                f"simulated={getattr(payload, 'simulated', '?')}): {exc}",
                exc=True,
            )

    async def _persist_forgotten_viewer_payment(self, payload: RoomMessagePayload) -> None:
        """不记录名单观众：弹幕与身份不落库，付费三表只存金额、类型与时刻。"""
        msg_type = payload.message_type
        if msg_type == "danmaku":
            return
        live_pk = await self._resolve_live_pk(payload.live_session_id)
        if live_pk is None:
            return
        if msg_type == "gift" and payload.gift is not None:
            gift = payload.gift
            await self.chat_repo.insert_gift(
                live_session_id=live_pk,
                timestamp_ms=payload.timestamp_ms,
                platform=payload.platform,
                user_id="",
                user_name="",
                gift_name=gift.name,
                quantity=int(gift.count),
                gift_id=gift.gift_id,
                unit_price=gift.unit_price,
                total_price=gift.total_price,
                paid_price=gift.paid_price,
                currency=gift.currency,
                raw_data=None,
                simulated=payload.simulated,
                viewer_id=None,
                id_kind="",
            )
            return
        if msg_type == "super_chat" and payload.sc is not None:
            sc = payload.sc
            await self.chat_repo.insert_super_chat(
                live_session_id=live_pk,
                timestamp_ms=payload.timestamp_ms,
                platform=payload.platform,
                user_id="",
                user_name="",
                message="",
                total_price=sc.total_price,
                currency=sc.currency,
                start_time=sc.start_time,
                end_time=sc.end_time,
                raw_data=None,
                simulated=payload.simulated,
                viewer_id=None,
                id_kind="",
            )
            return
        if msg_type == "guard" and payload.guard is not None:
            guard = payload.guard
            await self.chat_repo.insert_guard(
                live_session_id=live_pk,
                timestamp_ms=payload.timestamp_ms,
                platform=payload.platform,
                user_id="",
                user_name="",
                guard_level=guard.guard_level,
                guard_num=guard.guard_num,
                guard_unit=guard.guard_unit,
                total_price=guard.total_price,
                currency=guard.currency,
                raw_data=None,
                simulated=payload.simulated,
                viewer_id=None,
                id_kind="",
            )

    async def _on_streamer_speech(
        self,
        event_name: str,
        payload: StreamerSpeechPayload,
        source: str,
    ) -> None:
        """``streamer.speech`` 回调：遮名后写入 live_chat（带 utterance_id 与 memory_refs）。"""
        try:
            live_pk = await self._resolve_live_pk(payload.live_session_id)
            if live_pk is None:
                logger.debug(
                    f"{CoreEvents.STREAMER_SPEECH} 事件无法归属场次（未开启显式场次或未盖章），"
                    f"跳过落库（utterance_id={payload.utterance_id}）",
                )
                return
            masked_text = self._privacy.mask_speech(payload.text) if self._privacy is not None else payload.text
            memory_refs_json = json.dumps(payload.memory_refs, ensure_ascii=False) if payload.memory_refs else None
            await self.chat_repo.insert_live_chat(
                live_session_id=live_pk,
                timestamp_ms=payload.timestamp_ms,
                platform=self._speech_platform(),
                sender_role="assistant",
                sender_name="主播",
                content=masked_text,
                message_type="speak",
                reply_to_message_id=payload.reply_to_message_id or None,
                simulated=False,
                utterance_id=payload.utterance_id or None,
                memory_refs=memory_refs_json,
            )
            if payload.target_user_id:
                viewer_id = await self.viewer_repo.find_viewer_id(
                    platform=self._speech_platform(), external_id=payload.target_user_id
                )
                if viewer_id is None:
                    logger.debug(f"被回复的观众未建档，跳过被回复计数（target_user_id={payload.target_user_id}）")
                else:
                    viewer = await self.viewer_repo.get_viewer(viewer_id=viewer_id)
                    if viewer is not None and viewer.forget_requested_at_ms is not None:
                        logger.debug(f"被回复的观众处于宽限删除期，跳过被回复计数（viewer_id={viewer_id}）")
                    else:
                        await self.viewer_repo.record_replied(viewer_id=viewer_id, timestamp_ms=payload.timestamp_ms)
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志
            logger.error(
                f"StorageLedger 写入主播发言失败（event={event_name}, "
                f"utterance_id={getattr(payload, 'utterance_id', '?')}）：{exc}",
                exc=True,
            )

    async def _on_utterance_dropped(
        self,
        event_name: str,
        payload: UtteranceDroppedPayload,
        source: str,
    ) -> None:
        """``tts.utterance.dropped`` 回调：按 utterance_id 标记台词没送达。"""
        try:
            updated = await self.chat_repo.mark_utterance_dropped(
                utterance_id=payload.utterance_id,
                dropped_at_ms=payload.timestamp_ms,
            )
            if not updated:
                self._dropped_miss_count += 1
                logger.warning(
                    f"没送达标记找不到台词行（按已说过保留）: utterance_id={payload.utterance_id}, "
                    f"reason={payload.reason}, 本场累计未命中={self._dropped_miss_count}"
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"StorageLedger 写入没送达标记失败（utterance_id={payload.utterance_id}）: {exc}",
                exc=True,
            )

    async def _on_live_ended(
        self,
        event_name: str,
        payload: LiveEndedPayload,
        source: str,
    ) -> None:
        """``live.ended`` 回调：输出本场生产计数；没被主播 Agent 接管时执行下播清理。

        主播 Agent 在运行时接管（``set_cleanup_on_live_ended(False)``）：它要等道别那一轮
        落库、队尾台词播完、本场整理跑完才调 ``finish_stream_end``，这里只记生产计数，
        不提前清遮名、也不提前删除观众——否则道别句里的昵称会漏出、整理会读到已删的观众。
        """
        try:
            miss_count = self._dropped_miss_count
            self._dropped_miss_count = 0
            logger.info(f"场次 #{payload.live_session_id} 收播记账统计: 没送达标记找不到台词行次数={miss_count}")
            if not self._cleanup_on_live_ended:
                logger.debug(f"场次 #{payload.live_session_id} 的下播清理由主播 Agent 接管，记账器不在此清理")
                return
            await self.finish_stream_end()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"StorageLedger 下播收尾失败（session={payload.live_session_id}）: {exc}", exc=True)

    async def _on_game_event(
        self,
        event_name: str,
        payload: GamePayload,
        source: str,
    ) -> None:
        """``game.*`` 通配回调：游戏里程碑/安全阀/异常写入 game_events 表。"""
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
                save_id=payload.save_id,
                save_name=payload.save_name,
            )
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志
            logger.error(
                f"StorageLedger 写入游戏事件失败（event={event_name}, "
                f"event_type={getattr(payload, 'event_type', '?')}）：{exc}",
                exc=True,
            )

    async def _on_place_noted(
        self,
        event_name: str,
        payload: GamePlacePayload,
        source: str,
    ) -> None:
        """``game.place.noted`` 回调：地点动静写入 game_events（类型 ``place_<种类>``）。"""
        try:
            live_pk = await self._resolve_live_pk(payload.live_session_id)
            if live_pk is None:
                logger.debug(f"地点动静无法归属场次，跳过落库（kind={payload.kind}）")
                return
            await self.event_repo.insert_place_mark(
                live_session_id=live_pk,
                game=payload.game,
                kind=payload.kind,
                place=payload.place,
                distance=payload.distance,
                timestamp_ms=payload.timestamp_ms,
                save_id=payload.save_id,
                save_name=payload.save_name,
            )
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志
            logger.error(f"StorageLedger 写入地点动静失败（kind={getattr(payload, 'kind', '?')}）：{exc}", exc=True)

    async def _resolve_viewer(self, payload: RoomMessagePayload) -> Optional[int]:
        try:
            result = await self.viewer_repo.resolve_viewer(
                platform=payload.platform,
                id_kind=payload.user.id_kind,
                external_id=payload.user.id,
                open_app_id=payload.user.open_app_id,
                user_name=payload.user.name,
                timestamp_ms=payload.timestamp_ms,
                simulated=payload.simulated,
                name_match_window_ms=self._name_match_window_ms,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"观众身份解析失败，本条明细不带观众 ID、不计观众统计（{payload.platform}/{payload.user.id_kind}:"
                f"{payload.user.id}）：{exc}",
                exc=True,
            )
            return None
        if result.outcome == "linked_by_name":
            logger.info(
                f"新出现的观众 ID 按昵称关联到老观众（{payload.platform}/{payload.user.id_kind}:{payload.user.id}"
                f" → viewer {result.viewer_id}，昵称 {payload.user.name!r}）"
            )
        elif result.outcome == "pending":
            logger.info(
                f"新观众与老观众同名但超出关联时限或候选不唯一，已建档待人工确认（viewer {result.viewer_id}"
                f"，建议并入 viewer {result.candidate_id}，昵称 {payload.user.name!r}）"
            )
        return result.viewer_id

    def _speech_platform(self) -> str:
        if self._session_manager is None:
            return ""
        return getattr(self._session_manager, "platform", "") or ""

    async def _resolve_live_pk(self, stamped_pk: int = 0) -> Optional[int]:
        if stamped_pk and stamped_pk > 0:
            return stamped_pk
        if self._session_manager is None:
            return None
        try:
            return await self._session_manager.resolve_attribution_pk()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"场次归属解析失败，明细行跳过落库: {exc}")
            return None


__all__ = ["StorageLedger"]
