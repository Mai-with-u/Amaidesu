"""EventHistoryRecorder catch-all 记录测试

验证订阅全部事件后的记录契约：
- 长尾/动态族事件（``tts.utterance.*`` / ``tool.result.*``）无需逐一登记即被记录
- core 事件的 ``type`` 为精确事件名（直通，无 system.* 兼容名）
- ``room.message.*`` 折叠为 ``room.message``（唯一例外）
- 记录的 ``data`` 经开放载荷保留完整字段
"""

from __future__ import annotations

import asyncio

import pytest

from src.modules.events.event_history import EventHistoryService
from src.modules.events.event_recorder import EventHistoryRecorder
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    CoreErrorPayload,
    RoomMessagePayload,
    RoomMessageUser,
    ToolResultPayload,
    UtteranceStartedPayload,
)
from src.modules.events.event_bus import EventBus


@pytest.fixture
async def recorder_setup():
    """EventBus + 纯内存 EventHistoryService + 已启动的 catch-all 记录器。"""
    bus = EventBus()
    service = EventHistoryService(max_events=100)
    recorder = EventHistoryRecorder(event_bus=bus, event_history=service)
    await recorder.start()
    yield bus, service
    await recorder.stop()
    await bus.cleanup()


def _danmaku(content: str) -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id="u1", name="观众甲"),
        content=content,
    )


@pytest.mark.asyncio
async def test_catch_all_records_long_tail_events(recorder_setup) -> None:
    """tts.utterance.started 与 tool.result.speak 等长尾事件都被记录。"""
    bus, service = recorder_setup

    await bus.emit(
        CoreEvents.TTS_UTTERANCE_STARTED,
        UtteranceStartedPayload(utterance_id="utt-1", speech_text="大家好", engine="edge"),
        source="tts",
    )
    await bus.emit(
        "tool.result.speak",
        ToolResultPayload(tool_name="speak", status="success", result={"ok": True}),
        source="ToolRegistry",
    )
    await asyncio.sleep(0.05)

    types = {r.event_name for r in service.get_recent(50)}
    assert CoreEvents.TTS_UTTERANCE_STARTED in types
    assert "tool.result.speak" in types


@pytest.mark.asyncio
async def test_core_event_type_is_exact_name(recorder_setup) -> None:
    """core.error 的 type 为精确事件名（不再用 system.error 兼容名）。"""
    bus, service = recorder_setup

    await bus.emit(CoreEvents.CORE_ERROR, CoreErrorPayload(message="炸了"), source="qa")
    await asyncio.sleep(0.05)

    record = next(r for r in service.get_recent(50) if r.event_name == CoreEvents.CORE_ERROR)
    assert record.type == "core.error"
    assert record.level == "error"


@pytest.mark.asyncio
async def test_room_message_type_folded(recorder_setup) -> None:
    """room.message.* 记录的 type 折叠为 room.message。"""
    bus, service = recorder_setup

    await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, _danmaku("测试弹幕"), source="bilibili")
    await asyncio.sleep(0.05)

    record = next(r for r in service.get_recent(50) if r.event_name == CoreEvents.ROOM_MESSAGE_DANMAKU)
    assert record.type == "room.message"
    # 摘要带消息类型前缀 + 内容
    assert "[danmaku] 测试弹幕" == record.summary
    # 载荷完整保留（开放载荷不丢字段）
    assert record.data["content"] == "测试弹幕"
    assert record.data["user"]["name"] == "观众甲"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
