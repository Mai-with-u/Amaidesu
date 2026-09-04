"""
StorageLedger —— 直播间消息流落库记账器

与 EventHistoryRecorder 同类（订阅事件→处理），但写目标不同：
- EventHistoryRecorder → 事件历史服务（dashboard 用）
- StorageLedger       → SQLiteStore 业务表（live chat 行业标准数据平面）

## 职责
- 订阅 ``room.message.#``（MQTT 风格通配），按 payload.message_type 分发：
  - ``danmaku``   → live_chat
  - ``gift``      → gifts
  - ``super_chat`` → super_chats
  - ``enter``     → 当前 schema 无 enter 明细表 → debug 日志后丢弃（live_sessions 心跳与 enter 概念无关，独自走 session_manager，不在本层职责）
- 端到端贯通 ``simulated`` 字段：payload.simulated → 表列 simulated INTEGER
- 写入异常降级：单条失败 try/except 记 error 日志，不抛出、不影响主循环（即使记账器挂了，直播流也跑）

## 不做什么
- 不主动建 live_sessions 行（由未来的 session_manager 负责），这里只把
  payload.live_session_id（str）通过稳定 hash 映射为 INTEGER 主键。
- 不做统计查询（消费者层 ``WHERE simulated=0``）
- 不改 schema（表结构冻结在 v1；simulated 列已存在）

## 装配
- 由 main.py 组合根构造：传入 EventBus + SQLiteStore，调用 ``await ledger.start()``
- ``--dry`` 模式跳过订阅（保留构造便于冒烟，stop 仍可被调）
- run_shutdown 关闭链：放在 EventHistoryRecorder.stop 之后、EventBus.cleanup 之前
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Callable, Dict, Optional

from src.modules.events.payloads.room import (
    GiftInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)
from src.modules.logging import get_logger
from src.modules.storage.sqlite_store import SQLiteStore
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus


logger = get_logger("StorageLedger")


# 通配订阅名：覆盖 room.message.danmaku/gift/super_chat/enter 四类
_ROOM_MESSAGE_WILDCARD = "room.message.#"


class StorageLedger:
    """直播间行为流落库记账器。

    独立的 EventBus 订阅者；写目标 SQLiteStore（业务表 live_chat/gifts/super_chats）。
    与 EventHistoryRecorder 并存——后者写事件历史，前者写业务明细——不冲突。
    """

    def __init__(
        self,
        event_bus: "EventBus",
        sqlite_store: SQLiteStore,
    ) -> None:
        self.event_bus = event_bus
        self.sqlite_store = sqlite_store
        # per-instance 字符串→整数 session_pk 缓存（同进程内稳定）
        self._session_pk_cache: Dict[str, int] = {}
        # 订阅句柄表（stop 时按 event_name 取消）
        self._subscriptions: Dict[str, Callable] = {}
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
        self._started = True
        logger.info(
            f"StorageLedger 已订阅 {_ROOM_MESSAGE_WILDCARD}"
            "（danmaku→live_chat / gift→gifts / super_chat→super_chats / enter→debug 丢弃）",
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
        """
        try:
            msg_type = payload.message_type
            live_pk = self._session_pk(payload.live_session_id)
            if msg_type == "danmaku":
                await self.sqlite_store.insert_live_chat(
                    live_session_id=live_pk,
                    timestamp_ms=payload.timestamp_ms,
                    sender_role="viewer",
                    sender_id=payload.user.id,
                    sender_name=payload.user.name,
                    content=payload.content or "",
                    message_type=msg_type,
                    simulated=payload.simulated,
                )
                return
            if msg_type == "gift":
                gift = payload.gift
                if gift is None:
                    logger.debug("gift 事件 payload.gift 为空，跳过（采集器层补字段）")
                    return
                await self.sqlite_store.insert_gift(
                    live_session_id=live_pk,
                    timestamp_ms=payload.timestamp_ms,
                    user_id=payload.user.id,
                    user_name=payload.user.name,
                    gift_name=gift.name,
                    gift_count=int(gift.count),
                    simulated=payload.simulated,
                )
                return
            if msg_type == "super_chat":
                sc = payload.sc
                if sc is None:
                    logger.debug("super_chat 事件 payload.sc 为空，跳过")
                    return
                await self.sqlite_store.insert_super_chat(
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

    # -------------------- session_id 字符串 → live_chat.live_session_id INTEGER 映射 --------------------

    @staticmethod
    def _session_pk_to_int(session_id: str) -> int:
        """把 payload.live_session_id（str）映射为 live_chat.live_session_id（INTEGER）。

        策略：MD5 前 8 hex 字符 → 32 位无符号整数。稳定、跨进程一致、无外部依赖。
        数值无业务语义，仅做 FK；后续若建 live_sessions 索引表/反向查 map，
        可以同算法重算。

        注：直播业务一场session 一致性优先于 PK 美观；本算法稳定输出
        同一 str_id 同一 int_id，避免重复事件对应不同 PK 导致后续聚合失败。
        """
        digest = hashlib.md5(session_id.encode("utf-8")).hexdigest()[:8]
        return int(digest, 16)

    def _session_pk(self, session_id: str) -> int:
        cached = self._session_pk_cache.get(session_id)
        if cached is not None:
            return cached
        pk = self._session_pk_to_int(session_id)
        self._session_pk_cache[session_id] = pk
        return pk

    # -------------------- 辅助 --------------------

    def session_pk_cache_size(self) -> int:
        """返回已缓存的 session_id 数量（测试可读）。"""
        return len(self._session_pk_cache)


# ===== 辅助：仅测试用（构造 RoomMessagePayload 便利）=====


def make_room_message(
    *,
    message_type: str = "danmaku",
    session_id: str = "test_session",
    user: Optional[RoomMessageUser] = None,
    content: str = "",
    gift_name: str = "小星星",
    gift_count: int = 1,
    sc_amount: float = 50.0,
    simulated: bool = False,
    timestamp_ms: Optional[int] = None,
) -> RoomMessagePayload:
    """构造 ``RoomMessagePayload``：仅供测试/示例，避免重复样板。

    生产代码应走对应采集器/simulator 自然产出。
    """
    if user is None:
        user = RoomMessageUser(id="tester", name="测试观众")

    common: dict = {
        "live_session_id": session_id,
        "message_type": message_type,  # type: ignore[arg-type]
        "user": user,
        "content": content,
        "simulated": simulated,
    }
    if timestamp_ms is not None:
        common["timestamp_ms"] = timestamp_ms
    else:
        common["timestamp_ms"] = now_ms()

    if message_type == "gift":
        common["gift"] = GiftInfo(name=gift_name, count=gift_count)
    elif message_type == "super_chat":
        common["sc"] = SuperChatInfo(amount=sc_amount)

    return RoomMessagePayload(**common)


__all__ = ["StorageLedger", "make_room_message"]
