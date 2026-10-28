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
from src.agents.minecraft.state import MinecraftInstruction
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry

from .minecraft_v1_fakes import FakeMaicraft, assemble_minecraft_agent


def _agent(
    llm: Optional[Any] = None,
    *,
    live: Optional[bool] = None,
    mcp_provider: Optional[Any] = None,
    registry: Optional[Any] = None,
) -> tuple[MinecraftAgent, Any]:
    """构造未启动的 agent（直播闸初始态与私有 MCP 面经构造注入；返回 agent 与 LLM 供断言）。"""
    if llm is None:
        llm = MagicMock()
        llm.generate = AsyncMock()
    agent = assemble_minecraft_agent(llm=llm, live=live, mcp_provider=mcp_provider, registry=registry)
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
    agent, _llm = _agent(mcp_provider=FakeMaicraft())
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
    # 开播且游戏连上了（maicraft 有工具）才接委派；游戏没连上的拒收另有测试。
    # （私有 MCP 面在构造时注入，见 _agent 的 mcp_provider 参数）
    agent.receive_delegation(instruction="建房子", task_id="t2")
    assert len(agent._message_queue) == 1


async def test_offline_worker_never_starts_batch() -> None:
    """收工态下唤醒信号与队列残留都不会开新任务批。"""
    agent, llm = _agent(live=False)
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
    agent, llm = _agent(live=False)
    agent._message_queue.append(MinecraftInstruction("t1", "建房子"))
    await agent._run_task()
    llm.generate.assert_not_called()


async def test_inflight_batch_settles_then_stops() -> None:
    """收工信号落在在途批中间：当前步工具观察落盘后停止，不再发起新推理。"""
    agent_box: Dict[str, MinecraftAgent] = {}

    class _LiveCutProvider(BaseToolProvider):
        """工具执行途中拨下播信号的替身：第一次调用把直播闸切到收工。"""

        category = "game"
        name = "minecraft"

        def list_tools(self) -> list[ToolSpec]:
            return [
                ToolSpec(
                    name="notebook",
                    description="n",
                    parameters_schema={"type": "object"},
                    kind="sync",
                    provider="minecraft",
                    visible_to=["minecraft"],
                )
            ]

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            # 下播信号恰落在这一步工具执行期间（真实路径是 live.ended 事件，这里直写闸）
            agent_box["agent"]._live_active = False
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content={"ok": True})

    registry = ToolRegistry()
    registry.register_provider(_LiveCutProvider())
    agent, llm = _agent(live=True, registry=registry)
    agent_box["agent"] = agent
    agent._task_finished = False
    agent._task_instructions = ["建房子"]
    llm.generate = AsyncMock(side_effect=[_tool_call_response(), _tool_call_response()])

    await asyncio.wait_for(agent._run_task(), timeout=5)

    # 只发生了一次推理；工具回执已落进对话历史（状态落盘）
    assert llm.generate.await_count == 1
    tool_messages = [m for m in agent._messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
