"""直播生命周期收工闸测试。

live.started / live.ended 驱动 minecraft agent 的"直播中/收工"状态：
- 初始（含进程重启后）为收工态，直到开播信号
- 收工后不开新任务批、不响应递话与委派、退避定时器与注意流通知静默
- 在途任务批在当前 LLM 调用返回、工具观察落盘后停止，不硬取消
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.mcp.config import McpServerConfig
from src.agents.minecraft.state import MinecraftInstruction
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload


def _offline_config() -> MinecraftConfig:
    """不连私有 MCP 的配置：本机正开着 MaiCraft 时，测试也不能连上真实的 8766 端口。"""
    return MinecraftConfig(mcp=McpServerConfig(enabled=False, url="http://127.0.0.1:8766/mcp"))


def _agent(llm: Optional[Any] = None) -> tuple[MinecraftAgent, MagicMock]:
    """构造带 MagicMock 总线的 agent（返回 agent 与 LLM mock 供断言）。"""
    bus = MagicMock()
    bus.emit = AsyncMock()
    if llm is None:
        llm = MagicMock()
        llm.generate = AsyncMock()
    agent = MinecraftAgent(_offline_config(), llm_manager=llm, event_bus=bus)
    agent._running = True
    return agent, llm


def _started_payload(live_session_id: int = 7) -> LiveStartedPayload:
    return LiveStartedPayload(
        live_session_id=live_session_id,
        source="manual",
        title="测试场",
        room_id="1",
        platform="bilibili",
        started_at_ms=1,
    )


def _ended_payload(live_session_id: int = 7) -> LiveEndedPayload:
    return LiveEndedPayload(
        live_session_id=live_session_id,
        source="manual",
        reason="测试结束",
        duration_ms=100,
        empty_discarded=False,
        ended_at_ms=101,
    )


def _tool_call_response() -> Any:
    """一步带工具调用的 LLM 响应（content 置空，跳过 agent.replied 分支）。"""
    call = MagicMock()
    call.id = "c1"
    call.name = "minecraft_notebook"
    call.arguments = {"action": "read"}
    resp = MagicMock()
    resp.success = True
    resp.error = None
    resp.content = None
    resp.model = "m"
    resp.request_id = "r1"
    resp.tool_calls = [call]
    resp.usage_raw_json = None
    return resp


async def test_start_enters_offline_until_live_started() -> None:
    """进程重启后无活跃场次即收工态：递话拒收、委派不入队，直到开播信号。"""
    agent, _llm = _agent()
    await agent._on_start()
    assert agent._live_active is False
    agent._running = False
    agent._wake_event.set()
    if agent._worker_task is not None:
        with suppress(asyncio.CancelledError):
            await agent._worker_task
    assert agent.receive_prompt(content="继续干", source="operator") is False
    assert len(agent._message_queue) == 0
    agent.receive_delegation(instruction="建房子", task_id="t1")
    assert len(agent._message_queue) == 0
    await agent._on_live_started("live.started", _started_payload(), "test")
    agent.receive_delegation(instruction="建房子", task_id="t2")
    assert len(agent._message_queue) == 1


async def test_offline_worker_never_starts_batch() -> None:
    """收工态下唤醒信号与队列残留都不会开新任务批。"""
    agent, llm = _agent()
    agent._live_active = False
    agent._message_queue.append(MinecraftInstruction("t1", "建房子"))
    agent._wake_event.set()
    worker = asyncio.create_task(agent._worker())
    await asyncio.sleep(0.05)
    llm.generate.assert_not_called()
    agent._running = False
    agent._wake_event.set()
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass


async def test_offline_run_task_starts_no_inference() -> None:
    """收工后 _run_task 不发起任何 LLM 决策调用。"""
    agent, llm = _agent()
    agent._live_active = False
    agent._message_queue.append(MinecraftInstruction("t1", "建房子"))
    await agent._run_task()
    llm.generate.assert_not_called()


async def test_inflight_batch_settles_then_stops() -> None:
    """收工信号落在在途批中间：当前步工具观察落盘后停止，不再发起新推理。"""
    agent, llm = _agent()
    agent._live_active = True
    agent._task_finished = False
    agent._task_instructions = ["建房子"]
    llm.generate = AsyncMock(side_effect=[_tool_call_response(), _tool_call_response()])

    async def _fake_execute(name: str, arguments: Dict[str, Any], *, round_id: str = "") -> Dict[str, Any]:
        # 第一步工具执行期间收到下播信号：在途调用返回后本批收尾
        agent._live_active = False
        return {"ok": True}

    agent._execute_tool = AsyncMock(side_effect=_fake_execute)  # type: ignore[method-assign]

    await asyncio.wait_for(agent._run_task(), timeout=5)

    # 只发生了一次推理；工具回执已落进对话历史（状态落盘）
    assert llm.generate.await_count == 1
    tool_messages = [m for m in agent._messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
