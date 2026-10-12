"""命令线端到端测试（弹幕事件 → StreamerAgent → framework_delegate → 任务台账）。

与 test_command_wiring.py 的区别：走完整装配层——
- StreamerAgent 经 ``factory.instantiate_agent``（经 AgentManager.enable_agent）
- 真实 EventBus / ToolRegistry / TaskLedger / AgentManager
- framework provider 经 ``build_agent_control_provider`` 真实注册
- 仅 mock 必然的外部重资源：LLM 管理器与提示词管理器

minecraft 端用登记了 ``receive_delegation`` 的轻量替身 Agent 注册进
AgentManager（只验证收到委派/入台账，不跑真实游戏）。

覆盖：
- happy 链路：弹幕 /come → 台账出现该任务（initiator=streamer、
  executor=minecraft、source=agent、快照含映射语义目标），替身收到委派
- 拒绝面：白名单外命令不委派、限频第 4 条起拒绝、minecraft 未注册时
  受理失败不崩且记日志
"""

from __future__ import annotations
import asyncio

from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.agents.base import BaseAgent
from src.modules.agents.control import build_agent_control_provider
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger
from src.modules.time_utils import now_ms

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

    async def start_streamer(self, command_overrides: Optional[dict] = None) -> None:
        llm = MagicMock()
        llm.call_tools = AsyncMock()
        prompt = MagicMock()
        prompt.render = MagicMock(return_value="PROMPT")
        command = {"enabled": True, "mappings": dict(_COMMAND_MAPPINGS)}
        if command_overrides:
            command.update(command_overrides)
        config = {
            "proactive": {"enabled": False},
            "word_filter": {"enabled": False},
            "batch": {"batch_window_ms": 100, "tick_interval_ms": 50},
            "command": command,
        }
        ok = await self.manager.enable_agent(
            "streamer",
            config,
            llm_manager=llm,
            prompt_manager=prompt,
            event_bus=self.bus,
            tool_registry=self.registry,
        )
        assert ok, "streamer 装配应成功"

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


# =============================================================================
# happy 链路
# =============================================================================


@pytest.mark.asyncio
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
    assert record is not None, "台账应出现该委派任务"
    assert record.provider == "framework"
    assert record.tool == "framework_delegate"
    assert record.initiator == "streamer"
    assert record.executor == "minecraft"
    assert record.source == "agent"
    assert record.status == "accepted"
    assert record.snapshot["instruction"] == expected_instruction

    # 台账可通过 framework_task_status 查到（同一张表的完整链路）
    q = await harness.registry.invoke(
        ToolInvocation(tool_name="framework_task_status", arguments={"task_id": task_id}, source="test")
    )
    assert q.success is True
    assert q.structured_content["status"] == "accepted"


# =============================================================================
# 拒绝面
# =============================================================================


@pytest.mark.asyncio
async def test_unwhitelisted_command_no_delegation_no_task(harness: _Harness) -> None:
    """白名单外命令（/admin）端到端不委派、台账无新任务。"""
    await harness.start_streamer()

    await harness.emit_danmaku("/admin all")

    assert harness.minecraft_stub is not None
    assert harness.minecraft_stub.received == []
    assert len(harness.ledger) == 0


@pytest.mark.asyncio
async def test_rate_limit_rejects_from_fourth_command(harness: _Harness) -> None:
    """同一用户连发：默认 rate_max=3，第 4 条起不委派、台账不再增长。"""
    await harness.start_streamer()

    for _ in range(4):
        await harness.emit_danmaku("/come", user_id="u1")

    assert harness.minecraft_stub is not None
    assert len(harness.minecraft_stub.received) == 3, "前 3 条受理，第 4 条被限频拒绝"
    assert len(harness.ledger) == 3
    assert len({t for t, _ in harness.minecraft_stub.received}) == 3, "任务号互不相同"

    # 其他用户不受该用户限频影响
    await harness.emit_danmaku("/sleep", user_id="u2")
    assert len(harness.minecraft_stub.received) == 4
    assert len(harness.ledger) == 4


@pytest.mark.asyncio
async def test_minecraft_not_enabled_invoke_fails_no_crash(
    loguru_capture,
) -> None:
    """minecraft 未注册：受理失败、不崩、记日志、台账无新任务。"""
    h = _Harness(with_minecraft=False)
    try:
        with loguru_capture as cap:
            await h.start_streamer()
        await h.emit_danmaku("/come")

        assert len(h.ledger) == 0, "受理失败不登记台账"
        assert any("受理失败" in r["message"] for r in cap.records), "委派受理失败应记日志"
    finally:
        await h.teardown()
