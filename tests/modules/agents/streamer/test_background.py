"""BackgroundMaintainer 单元测试（§1.7 + §1.50 写入面）

聚焦 BackgroundMaintainer 新引入的 §1.50 记忆写入面：
- ``_summarize_topic`` 成功后调 ``memory.ingest(text=summary, source="topic_summary")``
  ，摘要为空时不调；
- 高价值事件（礼物 / SC）→ ``_handle_memory_event`` → ``memory.ingest``，
  含 60 秒同用户去抖。

不做后台循环端到端——那是 integration 测试范畴（参 ``tests/integration/``）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.background import BackgroundMaintainer, _EVENT_INGEST_DEBOUNCE_MS
from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.room import (
    GiftInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


def _make_gift_payload(user_id: str = "u1", user_name: str = "观众A") -> RoomMessagePayload:
    """构造礼物事件 payload。"""
    return RoomMessagePayload(
        live_session_id="live",
        message_type="gift",
        user=RoomMessageUser(id=user_id, name=user_name),
        content="",
        gift=GiftInfo(name="小星星", count=1),
    )


def _make_sc_payload(
    text: str = "主播加油",
    amount: float = 50.0,
    user_id: str = "u2",
    user_name: str = "SC用户",
) -> RoomMessagePayload:
    """构造 SC 事件 payload。"""
    return RoomMessagePayload(
        live_session_id="live",
        message_type="super_chat",
        user=RoomMessageUser(id=user_id, name=user_name),
        content=text,
        sc=SuperChatInfo(amount=amount),
    )


def _make_maintainer(
    *,
    memory: MagicMock | None = None,
    event_bus: EventBus | None = None,
) -> tuple[BackgroundMaintainer, MagicMock]:
    """构造一个带 mock 记忆的 BackgroundMaintainer。

    room_state / llm / context 都给最简实现（_summarize_topic 走真实路径
    不需要——本测试只盯 §1.50 写入面）。
    """
    room_state = MagicMock()
    rs_snap = MagicMock()
    rs_snap.heat = "low"
    rs_snap.topics = []
    rs_snap.sc_queue = []
    rs_snap.topic_summary = ""
    rs_snap.topic_summary_at_ms = 0
    rs_snap.last_update_ms = 1
    room_state.get_snapshot = MagicMock(return_value=rs_snap)

    if memory is None:
        memory = MagicMock()
        memory.ingest = AsyncMock(return_value=None)
    else:
        memory.ingest = AsyncMock(return_value=None)

    return (
        BackgroundMaintainer(
            config={},
            room_state=room_state,
            memory=memory,
            event_bus=event_bus,
        ),
        memory,
    )


# ---------------------------------------------------------------------------
# §1.50 摘要 ingest
# ---------------------------------------------------------------------------


class TestBackgroundTopicSummaryIngest:
    """``_summarize_topic`` 成功后调 ``memory.ingest`` 的契约测试。"""

    @pytest.mark.asyncio
    async def test_ingest_topic_summary_calls_memory_with_summary(self) -> None:
        """摘要非空 → ingest 被调（text=summary, source="topic_summary"）。"""
        maintainer, memory = _make_maintainer()

        await maintainer._ingest_topic_summary("观众在聊游戏剧情")

        memory.ingest.assert_awaited_once_with(
            text="观众在聊游戏剧情",
            source="topic_summary",
            tags=["topic", "auto_summary"],
        )

    @pytest.mark.asyncio
    async def test_ingest_topic_summary_skips_when_summary_empty(self) -> None:
        """summary 空字符串 → ingest **不**被调（防死循环占位）。"""
        maintainer, memory = _make_maintainer()

        await maintainer._ingest_topic_summary("")

        memory.ingest.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ingest_topic_summary_swallows_memory_exception(self) -> None:
        """memory.ingest 抛异常 → 不阻断主循环（仅 logger.warning）。"""
        maintainer, memory = _make_maintainer()
        memory.ingest = AsyncMock(side_effect=RuntimeError("vector store 写满了"))

        # 不应抛
        await maintainer._ingest_topic_summary("观众在聊游戏剧情")

        memory.ingest.assert_awaited_once()


# ---------------------------------------------------------------------------
# §1.50 高价值事件 ingest
# ---------------------------------------------------------------------------


class TestBackgroundHighValueEventIngest:
    """礼物 / SC 事件 → ``_handle_memory_event`` → 格式化中文事实 + ingest。"""

    @pytest.mark.asyncio
    async def test_gift_event_writes_fact_text(self) -> None:
        """gift 事件 → 中文事实 "观众A 送出礼物 小星星" + source="live_event" tag=["gift"]。"""
        maintainer, memory = _make_maintainer()

        payload = _make_gift_payload()
        await maintainer._handle_memory_event(
            "room.message.gift",
            payload,
            source="test",
        )

        memory.ingest.assert_awaited_once()
        call = memory.ingest.await_args
        assert call.kwargs["text"] == "观众A 送出礼物 小星星"
        assert call.kwargs["source"] == "live_event"
        assert call.kwargs["tags"] == ["gift"]

    @pytest.mark.asyncio
    async def test_gift_event_with_count_shows_multiplier(self) -> None:
        """礼物 count > 1 时事实末尾加 "（×N）"。"""
        maintainer, memory = _make_maintainer()
        payload = RoomMessagePayload(
            live_session_id="live",
            message_type="gift",
            user=RoomMessageUser(id="u1", name="A"),
            content="",
            gift=GiftInfo(name="小星星", count=10),
        )

        await maintainer._handle_memory_event("room.message.gift", payload, source="test")

        call = memory.ingest.await_args
        assert call.kwargs["text"] == "A 送出礼物 小星星（×10）"

    @pytest.mark.asyncio
    async def test_super_chat_event_writes_fact_with_amount(self) -> None:
        """SC 事件 → "昵称 发送 SC（¥金额）：内容" + tag=["super_chat"]。"""
        maintainer, memory = _make_maintainer()

        payload = _make_sc_payload(text="主播加油！", amount=50.0)
        await maintainer._handle_memory_event(
            "room.message.super_chat",
            payload,
            source="test",
        )

        memory.ingest.assert_awaited_once()
        call = memory.ingest.await_args
        assert call.kwargs["text"] == "SC用户 发送 SC（¥50）：主播加油！"
        assert call.kwargs["source"] == "live_event"
        assert call.kwargs["tags"] == ["super_chat"]

    @pytest.mark.asyncio
    async def test_super_chat_without_text_still_ingests(self) -> None:
        """SC content 为空时仍写事实（"发送 SC（¥金额）"）。"""
        maintainer, memory = _make_maintainer()
        payload = _make_sc_payload(text="", amount=30.0)

        await maintainer._handle_memory_event(
            "room.message.super_chat",
            payload,
            source="test",
        )

        memory.ingest.assert_awaited_once()
        call = memory.ingest.await_args
        assert call.kwargs["text"] == "SC用户 发送 SC（¥30）"

    @pytest.mark.asyncio
    async def test_debounce_60s_drops_subsequent_event_for_same_user(self) -> None:
        """同用户 60 秒内第二次事件被丢弃（仅首次 ingest）。"""
        maintainer, memory = _make_maintainer()

        payload = _make_gift_payload(user_id="u_noisy", user_name="噪音哥")
        await maintainer._handle_memory_event("room.message.gift", payload, source="test")
        # 紧接着第二次同用户触发，应被去抖挡掉
        await maintainer._handle_memory_event("room.message.gift", payload, source="test")

        memory.ingest.assert_awaited_once()
        assert maintainer._last_ingest_ms["u_noisy"] > 0

    @pytest.mark.asyncio
    async def test_debounce_zero_ms_expiry_re_ingests(self) -> None:
        """去抖时间窗口内的，不重复写；模拟"超过 60 秒"则恢复。"""
        maintainer, memory = _make_maintainer()
        # 手动回拨 last_ingest_ms 模拟 60 秒前写过
        maintainer._last_ingest_ms = {"u_old": 0}

        payload = _make_gift_payload(user_id="u_old", user_name="老用户")
        await maintainer._handle_memory_event("room.message.gift", payload, source="test")

        # 第一次应被放行（last=0 → 任何当前时间都通过检查）
        memory.ingest.assert_awaited_once()
        # 在已写入的情况下重置 last 为 0，让第二次再过
        maintainer._last_ingest_ms["u_old"] = 0
        await maintainer._handle_memory_event("room.message.gift", payload, source="test")
        assert memory.ingest.await_count == 2

    @pytest.mark.asyncio
    async def test_different_users_each_get_ingested(self) -> None:
        """不同 user_id 各自独立去抖——应都被写入。"""
        maintainer, memory = _make_maintainer()

        payload_a = _make_gift_payload(user_id="u_a", user_name="甲")
        payload_b = _make_gift_payload(user_id="u_b", user_name="乙")

        await maintainer._handle_memory_event("room.message.gift", payload_a, source="test")
        await maintainer._handle_memory_event("room.message.gift", payload_b, source="test")

        assert memory.ingest.await_count == 2
        text_a = memory.ingest.await_args_list[0].kwargs["text"]
        text_b = memory.ingest.await_args_list[1].kwargs["text"]
        assert "甲" in text_a
        assert "乙" in text_b

    @pytest.mark.asyncio
    async def test_danmaku_event_skipped_by_message_type(self) -> None:
        """非 gift/SC 事件（如 danmaku）由 message_type gate 直接跳过，ingest 不被调。"""
        maintainer, memory = _make_maintainer()
        payload = RoomMessagePayload(
            live_session_id="live",
            message_type="danmaku",
            user=RoomMessageUser(id="u1", name="A"),
            content="主播好可爱",
        )

        await maintainer._handle_memory_event(
            "room.message.danmaku",
            payload,
            source="test",
        )

        memory.ingest.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_memory_ingest_exception_does_not_propagate(self) -> None:
        """memory.ingest 抛异常 → _handle_memory_event 不抛（防阻断记账）。"""
        maintainer, memory = _make_maintainer()
        memory.ingest = AsyncMock(side_effect=RuntimeError("vector store 炸"))

        # 直接调——不应抛
        await maintainer._handle_memory_event(
            "room.message.gift",
            _make_gift_payload(),
            source="test",
        )
        memory.ingest.assert_awaited_once()


# ---------------------------------------------------------------------------
# §1.50 事件订阅生命周期
# ---------------------------------------------------------------------------


class TestBackgroundSubscribeGuard:
    """start() 阶段订阅礼物/SC；去重订阅；无 memory / event_bus 时跳过。"""

    @staticmethod
    def _wrapped_handlers(bus: EventBus, event_name: str) -> list:
        """取出 EventBus 在 event_name 上 HandlerWrapper 的两个候选 handler 字段。"""
        return [
            e
            for e in bus._handlers.get(event_name, [])  # type: ignore[attr-defined]
        ]

    @pytest.mark.asyncio
    async def test_start_subscribes_when_memory_and_event_bus_provided(self) -> None:
        """memory + event_bus 双注入 → 订阅被挂上。"""
        bus = EventBus()
        maintainer, _memory = _make_maintainer(event_bus=bus)

        await maintainer.start()

        try:
            assert maintainer._subscribed is True
            # EventBus.on 用 typed_wrapper 包裹原 handler——同时探测
            # ``.original_handler``（事件总线公开的原始入口）和 ``.handler``
            gift_handlers = self._wrapped_handlers(bus, "room.message.gift")  # type: ignore[has-type]
            sc_handlers = self._wrapped_handlers(bus, "room.message.super_chat")  # type: ignore[has-type]
            for entries in (gift_handlers, sc_handlers):
                original = [
                    getattr(e, "original_handler", None) for e in entries
                ]
                wrapped = [
                    getattr(e, "handler", None) for e in entries
                ]
                assert maintainer._handle_memory_event in original
                # typed_wrapper 必须有，验证 EventBus 已接管路由
                assert any(wrapped)
        finally:
            await maintainer.stop()

    @pytest.mark.asyncio
    async def test_start_without_event_bus_skips_subscription(self) -> None:
        """event_bus=None → 不订阅（_subscribed 标记仍 False）。"""
        maintainer, _memory = _make_maintainer(event_bus=None)

        await maintainer.start()

        try:
            assert getattr(maintainer, "_subscribed", False) is False
        finally:
            await maintainer.stop()

    @pytest.mark.asyncio
    async def test_double_start_does_not_double_subscribe(self) -> None:
        """重复 start() 防重订阅：_subscribed 守门。"""
        bus = EventBus()
        maintainer, _memory = _make_maintainer(event_bus=bus)

        await maintainer.start()
        await maintainer.start()  # 第二次不应再加挂

        try:
            gift_handlers = self._wrapped_handlers(bus, "room.message.gift")  # type: ignore[has-type]
            sc_handlers = self._wrapped_handlers(bus, "room.message.super_chat")  # type: ignore[has-type]
            for entries in (gift_handlers, sc_handlers):
                original = [
                    getattr(e, "original_handler", None) for e in entries
                ]
                assert original.count(maintainer._handle_memory_event) == 1
        finally:
            await maintainer.stop()


def test_event_ingest_debounce_window_constant() -> None:
    """60 秒去抖窗口常量是 60_000 ms（防回归）。"""
    assert _EVENT_INGEST_DEBOUNCE_MS == 60_000
