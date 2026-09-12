"""世界窗口（观众上下文）测试：live_chat 读取 + per-persona 裁剪 + 格式化。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.simulator.service import SimulatorService
from src.modules.simulator.types import Persona, PersonaRole
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="world-window-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


# 测试用场次管理器替身：固定返回预置主键（模拟"当前场次"解析结果）
SESSION_PK = 888


class _FakeSessionManager:
    async def resolve_pk(self) -> int:
        return SESSION_PK


class _FakeConfigService:
    """仅暴露 main_config 的最小 ConfigService 替身"""

    def __init__(self) -> None:
        self.main_config = {"simulator": {"enabled": False}}


async def _setup_service(store: SQLiteDatabase) -> SimulatorService:
    """构造并 setup 一个不自动启动的 SimulatorService"""
    service = SimulatorService(event_bus=EventBus(), sim_repo=store.sim, chat_repo=store.chat, event_repo=store.events, session_manager=_FakeSessionManager())
    await service.setup(_FakeConfigService())
    return service


async def _seed_chat(store: SQLiteDatabase, count: int = 20) -> None:
    """写入 count 条消息到 live_chat 当前场次（含主播发言，公共流混合）"""
    pk = SESSION_PK
    base_ts = 1_700_000_000_000
    for i in range(count):
        role = "assistant" if i % 5 == 0 else "viewer"
        sender = "主播" if role == "assistant" else f"观众{i}"
        sender_id = "streamer" if role == "assistant" else f"uid_{i}"
        await store.chat.insert_live_chat(
            live_session_id=pk,
            timestamp_ms=base_ts + i * 1000,
            sender_role=role,
            sender_id=sender_id,
            sender_name=sender,
            content=f"消息{i}",
            message_type="danmaku",
        )


def _persona(role: PersonaRole, window: int | None = None) -> Persona:
    return Persona(
        user_id="test_user",
        user_nickname="测试观众",
        role=role,
        personality="p",
        speaking_style="s",
        context_window_size=window,
    )


@pytest.mark.asyncio
async def test_window_reads_mixed_stream(store: SQLiteDatabase) -> None:
    """窗口同时包含观众弹幕与主播发言（公共流语义）。"""
    await _seed_chat(store, count=10)
    service = await _setup_service(store)

    window = await service._fetch_world_window(persona=_persona(PersonaRole.FAN))

    assert any(line.startswith("主播:") for line in window), "主播发言应进入公共流窗口"
    assert any(line.startswith("观众") for line in window), "观众弹幕应进入公共流窗口"
    assert all(": " in line for line in window)


@pytest.mark.asyncio
async def test_window_per_role_defaults(store: SQLiteDatabase) -> None:
    """角色默认窗口：veteran 看得多、passerby 看得少。"""
    await _seed_chat(store, count=20)
    service = await _setup_service(store)

    veteran_window = await service._fetch_world_window(persona=_persona(PersonaRole.VETERAN))
    passerby_window = await service._fetch_world_window(persona=_persona(PersonaRole.PASSERBY))

    assert len(veteran_window) == 12
    assert len(passerby_window) == 2


@pytest.mark.asyncio
async def test_window_persona_override_wins(store: SQLiteDatabase) -> None:
    """persona 级 context_window_size 覆盖角色默认。"""
    await _seed_chat(store, count=20)
    service = await _setup_service(store)

    window = await service._fetch_world_window(
        persona=_persona(PersonaRole.VETERAN, window=3),
    )
    assert len(window) == 3


@pytest.mark.asyncio
async def test_window_latest_messages_last(store: SQLiteDatabase) -> None:
    """窗口按时间正序（最新在末尾），与对话顺序一致。"""
    await _seed_chat(store, count=20)
    service = await _setup_service(store)

    window = await service._fetch_world_window(persona=_persona(PersonaRole.VETERAN))
    assert window[-1].endswith("消息19"), "最新消息应在窗口末尾"


@pytest.mark.asyncio
async def test_window_session_isolation(store: SQLiteDatabase) -> None:
    """窗口按场次隔离：其他场次的消息不进入。"""
    await _seed_chat(store, count=5)
    other_pk = SESSION_PK + 1
    await store.chat.insert_live_chat(
        live_session_id=other_pk,
        timestamp_ms=1_700_000_999_000,
        sender_role="viewer",
        sender_id="uid_x",
        sender_name="别场观众",
        content="别场消息",
        message_type="danmaku",
    )
    service = await _setup_service(store)

    window = await service._fetch_world_window(persona=_persona(PersonaRole.FAN))
    assert all("别场" not in line for line in window)
