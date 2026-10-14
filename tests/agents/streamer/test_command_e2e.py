"""命令线端到端：弹幕事件 → 经 AgentManager 装配的 StreamerAgent → framework_delegate → 任务台账。

真实 EventBus / ToolRegistry / TaskLedger / AgentManager，只替换 LLM 与提示词；minecraft 用只登记委派的替身。
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import pytest

from src.modules.agents.base import BaseAgent
from src.modules.agents.control import build_agent_control_provider
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger
from tests.agents.streamer.helpers import stub_llm, stub_prompt

# 与 [agents.streamer.command] 生产起步配置一致的映射语义
_COME_INSTRUCTION = "到观众这儿来（跟随观众）"
_SLEEP_INSTRUCTION = "去睡觉"
_COMMAND_MAPPINGS = {"come": _COME_INSTRUCTION, "sleep": _SLEEP_INSTRUCTION}


class _MinecraftStubAgent(BaseAgent):
    """minecraft 轻量替身：只登记收到的委派，不执行真实游戏逻辑。"""

    name = "minecraft"
    description = "minecraft stub"

    def __init__(self) -> None:
        self.__class__ = type(
            "_MinecraftStubAgent_instance",
            (type(self),),
            {"name": "minecraft", "description": "minecraft stub"},
        )
        super().__init__()
        self.received: List[tuple[str, str]] = []  # (task_id, instruction)

    def list_tools(self):
        return []

    def receive_delegation(self, *, instruction: str, task_id: str):
        self.received.append((task_id, instruction))
        return None


class _Harness:
    """一次端到端装配：真实 bus/registry/ledger/manager + 替身 LLM/Prompt。"""

    def __init__(self, *, with_minecraft: bool = True) -> None:
        self.bus = EventBus(enable_stats=False)
        self.registry = ToolRegistry(event_bus=self.bus)
        self.ledger = TaskLedger(event_bus=self.bus)
        self.manager = AgentManager()
        self.registry.register_provider(build_agent_control_provider(self.manager, self.ledger))
        self.minecraft_stub: Optional[_MinecraftStubAgent] = None
        if with_minecraft:
            self.minecraft_stub = _MinecraftStubAgent()
            self.manager.register(self.minecraft_stub)

    async def start_streamer(self) -> None:
        config = {
            "proactive": {"enabled": False},
            "word_filter": {"enabled": False},
            "command": {"enabled": True, "mappings": dict(_COMMAND_MAPPINGS)},
        }
        assert await self.manager.enable_agent(
            "streamer",
            config,
            llm_manager=stub_llm(),
            prompt_manager=stub_prompt(),
            event_bus=self.bus,
            tool_registry=self.registry,
        )

    async def emit_danmaku(self, text: str, user_id: str = "u1") -> None:
        payload = RoomMessagePayload(
            message_type="danmaku",
            user=RoomMessageUser(id=user_id, name="测试观众"),
            content=text,
            timestamp_ms=now_ms(),
        )
        await self.bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, payload, source="E2ETest")
        await asyncio.sleep(0.05)

    async def teardown(self) -> None:
        await self.manager.stop_all()
        for name in list(self.manager.list_agents()):
            self.manager.unregister(name)

    def ledger_task_ids(self) -> List[str]:
        return list(self.ledger.active_task_ids())


@pytest.fixture
async def harness():
    h = _Harness()
    yield h
    await h.teardown()


# happy 链路


@pytest.mark.parametrize(
    ("text", "expected_instruction"),
    [
        ("/come", _COME_INSTRUCTION),
        ("/sleep", _SLEEP_INSTRUCTION),
    ],
)
async def test_danmaku_command_delegates_and_registers_task(
    harness: _Harness, text: str, expected_instruction: str
) -> None:
    """弹幕命令 → 委派受理 → 台账登记（initiator/executor/source/快照按契约）。"""
    await harness.start_streamer()

    await harness.emit_danmaku(text)

    assert harness.minecraft_stub is not None
    assert len(harness.minecraft_stub.received) == 1
    task_id, instruction = harness.minecraft_stub.received[0]
    assert instruction == expected_instruction

    record = harness.ledger.get(task_id)
    assert (record.provider, record.tool, record.status) == ("framework", "framework_delegate", "accepted")
    assert (record.initiator, record.executor, record.source) == ("streamer", "minecraft", "agent")
    assert record.snapshot["instruction"] == expected_instruction

    # 台账可通过 framework_task_status 查到（同一张表的完整链路）
    q = await harness.registry.invoke(
        ToolInvocation(tool_name="framework_task_status", arguments={"task_id": task_id}, source="test")
    )
    assert q.success is True
    assert q.structured_content["status"] == "accepted"


async def test_unwhitelisted_and_rate_limited_commands_do_not_delegate(harness: _Harness) -> None:
    """白名单外不委派；同一用户默认 rate_max=3，第 4 条起不委派，其他用户不受影响。"""
    await harness.start_streamer()
    await harness.emit_danmaku("/admin all")
    assert harness.minecraft_stub.received == [] and len(harness.ledger) == 0

    for _ in range(4):
        await harness.emit_danmaku("/come", user_id="u1")
    assert len(harness.ledger) == 3
    assert len({task_id for task_id, _ in harness.minecraft_stub.received}) == 3
    await harness.emit_danmaku("/sleep", user_id="u2")
    assert len(harness.minecraft_stub.received) == len(harness.ledger) == 4


async def test_minecraft_not_enabled_rejects_without_crash(loguru_capture) -> None:
    h = _Harness(with_minecraft=False)
    try:
        await h.start_streamer()
        await h.emit_danmaku("/come")
        assert len(h.ledger) == 0
        assert any("受理失败" in r["message"] for r in loguru_capture.records)
    finally:
        await h.teardown()
