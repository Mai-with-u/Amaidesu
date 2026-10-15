"""世界窗口（观众上下文）：读取当前场次完整公共流（观众弹幕 + 主播发言），按时间正序，不混入其他场次"""

from __future__ import annotations

from src.modules.events.event_bus import EventBus
from src.modules.simulator.service import SimulatorService
from src.modules.simulator.types import Persona, PersonaRole
from src.modules.storage.database import SQLiteDatabase
from tests.modules.simulator.helpers import FakeConfigService

SESSION_PK = 888  # 固定的"当前场次"解析结果


class _FakeSessionManager:
    async def resolve_pk(self) -> int:
        return SESSION_PK


async def _service_with_chat(store: SQLiteDatabase, count: int) -> SimulatorService:
    """写入 count 条消息到当前场次（每五条一条主播发言），并 setup 一个不自动启动的服务。"""
    for i in range(count):
        is_streamer = i % 5 == 0
        await store.chat.insert_live_chat(
            live_session_id=SESSION_PK,
            timestamp_ms=1_700_000_000_000 + i * 1000,
            sender_role="assistant" if is_streamer else "viewer",
            sender_id="streamer" if is_streamer else f"uid_{i}",
            sender_name="主播" if is_streamer else f"观众{i}",
            content=f"消息{i}",
            message_type="danmaku",
        )
    service = SimulatorService(
        event_bus=EventBus(), sim_repo=store.sim, chat_repo=store.chat, session_manager=_FakeSessionManager()
    )
    await service.setup(FakeConfigService({"enabled": False}))
    return service


def _persona(role: PersonaRole) -> Persona:
    return Persona(user_id="test_user", user_nickname="测试观众", role=role, personality="p", speaking_style="s")


async def test_window_is_the_full_mixed_stream_in_chronological_order_for_every_role(store: SQLiteDatabase) -> None:
    service = await _service_with_chat(store, 60)  # 超过旧版条数上限也完整返回
    for role in (PersonaRole.FAN, PersonaRole.VETERAN, PersonaRole.PASSERBY):  # 各角色读同一公共流，由人设决定怎么回应
        window = await service._fetch_world_window(persona=_persona(role))
        assert len(window) == 60 and all(": " in line for line in window)
        assert window[0].endswith("消息0") and window[-1].endswith("消息59")  # 最新在末尾，与对话顺序一致
        assert any(line.startswith("主播:") for line in window) and any(line.startswith("观众") for line in window)


async def test_window_excludes_other_sessions(store: SQLiteDatabase) -> None:
    service = await _service_with_chat(store, 5)
    await store.chat.insert_live_chat(
        live_session_id=SESSION_PK + 1,
        timestamp_ms=1_700_000_999_000,
        sender_role="viewer",
        sender_id="uid_x",
        sender_name="别场观众",
        content="别场消息",
        message_type="danmaku",
    )
    window = await service._fetch_world_window(persona=_persona(PersonaRole.FAN))
    assert len(window) == 5 and all("别场" not in line for line in window)


async def test_empty_session_gives_empty_window(store: SQLiteDatabase) -> None:
    service = await _service_with_chat(store, 0)
    assert await service._fetch_world_window(persona=_persona(PersonaRole.FAN)) == []
