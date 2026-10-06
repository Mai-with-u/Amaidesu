"""新任务开局由宿主代读现状、周边、地标、笔记和能力签名，模型不必用开头几轮请求逐项读取。

实测新任务的前三轮请求都在读当前状态、地标、笔记和待办，第一场还为能力契约单独请求了三十多次。
"""

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.registry import ToolRegistry


def make_agent() -> tuple[MinecraftAgent, List[tuple[str, Dict[str, Any]]]]:
    """工具调用全部记录并返回预置回执，不连接游戏。"""
    bus = MagicMock()
    bus.emit = AsyncMock()
    agent = MinecraftAgent(MinecraftConfig(), llm_manager=MagicMock(), event_bus=bus, tool_registry=ToolRegistry())
    agent._register_tools()
    agent._perceive_tool = "maicraft_perceive"
    invoked: List[tuple[str, Dict[str, Any]]] = []

    async def _execute(name: str, arguments: Dict[str, Any], *, round_id: str = "") -> Dict[str, Any]:
        invoked.append((name, dict(arguments)))
        if arguments.get("detail") == "signatures":
            return {"semantic_abilities": [{"ability": "maicraft:travel", "parameters": {"destination": "object"}}]}
        if arguments.get("view") == "landmarks":
            return {"landmarks": [{"label": "蜂房机器"}]}
        if arguments.get("action") == "read":
            return {"notebook": agent._mc_state.notebook}
        return {"view": arguments.get("view"), "position": {"x": -86, "y": 105, "z": 27}}

    agent._execute_tool = _execute  # type: ignore[method-assign]
    return agent, invoked


@pytest.mark.asyncio
async def test_opening_bundle_reads_fixed_facts_once_per_task() -> None:
    """开局一次代读五项资料，作为一组工具调用与回执进入历史；能力签名同一连接内只读一次。"""
    agent, invoked = make_agent()
    agent._mc_state.set_notebook("蜂房在停机开关旁边")
    messages: List[Dict[str, Any]] = [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "做一个蜂蜜胶"}]

    await agent._append_opening_bundle(messages)

    views = [arguments.get("view") or arguments.get("action") for _, arguments in invoked]
    assert views == ["situation", "surroundings", "landmarks", "read", "abilities"]
    assistant = messages[2]
    assert assistant["role"] == "assistant" and assistant["content"].startswith("[开局资料]")
    assert len(assistant["tool_calls"]) == 5
    tool_ids = [message["tool_call_id"] for message in messages[3:]]
    assert tool_ids == [call["id"] for call in assistant["tool_calls"]]
    assert '"蜂房机器"' in messages[5]["content"]

    # 同一连接的下一个任务复用已读签名，不再请求；连接恢复后重新读取。
    invoked.clear()
    await agent._append_opening_bundle([{"role": "system", "content": "SYSTEM"}])
    assert [arguments.get("detail") for _, arguments in invoked].count("signatures") == 0
    agent._on_mcp_recovered()
    invoked.clear()
    await agent._append_opening_bundle([{"role": "system", "content": "SYSTEM"}])
    assert [arguments.get("detail") for _, arguments in invoked].count("signatures") == 1


@pytest.mark.asyncio
async def test_opening_bundle_skips_empty_notebook_and_missing_connection() -> None:
    """笔记为空不代读；游戏连接未就绪时不发 MCP 读取，也不追加空的开局消息。"""
    agent, invoked = make_agent()
    messages: List[Dict[str, Any]] = [{"role": "system", "content": "SYSTEM"}]
    await agent._append_opening_bundle(messages)
    assert "read" not in [arguments.get("action") for _, arguments in invoked]

    agent, invoked = make_agent()
    agent._perceive_tool = None
    messages = [{"role": "system", "content": "SYSTEM"}]
    await agent._append_opening_bundle(messages)
    assert invoked == [] and messages == [{"role": "system", "content": "SYSTEM"}]


def test_prompt_describes_opening_bundle() -> None:
    """提示词说明开局资料已代读、按能力签名填参数。"""
    reset_prompt_manager()
    try:
        prompt = get_prompt_manager().render("amaidesu_minecraft_agent")
    finally:
        reset_prompt_manager()
    assert "[开局资料]" in prompt and "按能力签名填写参数" in prompt
