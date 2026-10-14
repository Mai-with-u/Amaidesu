"""观众命令在 StreamerAgent 上的接线：命令被消费不进决策缓冲，配置即白名单，命令解析不进工具面"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.registry import ToolRegistry
from tests.agents.streamer.helpers import make_streamer_agent

_MAPPINGS = {"come": "到主播身边来", "sleep": "回床睡觉"}


def _agent(command: Optional[dict] = None, registry=None, accepted: bool = True):
    if registry is None:
        registry = MagicMock()
        registry.invoke = AsyncMock(
            return_value=ToolExecutionResult(
                tool_name="framework_delegate",
                success=accepted,
                structured_content={"accepted": True, "task_id": "deleg_1"} if accepted else None,
                error_message="" if accepted else "受理失败：目标 Agent 'minecraft' 不在名册",
            )
        )
    section = {"enabled": True, "mappings": dict(_MAPPINGS), **(command or {})}
    return make_streamer_agent({"command": section}, tool_registry=registry), registry


def _danmaku(content: str) -> RoomMessagePayload:
    user = RoomMessageUser(id="u1", name="测试观众")
    return RoomMessagePayload(message_type="danmaku", user=user, content=content, timestamp_ms=now_ms())


@pytest.mark.parametrize(("text", "instruction"), [("/come", "到主播身边来"), ("/sleep", "回床睡觉")])
async def test_whitelisted_command_delegates_instead_of_buffering(text: str, instruction: str) -> None:
    agent, registry = _agent()
    await agent.handle_message(_danmaku(text))

    invocation = registry.invoke.call_args.args[0]
    assert (invocation.tool_name, invocation.source) == ("framework_delegate", "streamer")
    assert invocation.arguments == {"agent": "", "instruction": instruction}
    assert agent._buffer.size == 0 and agent.get_statistics()["total_messages"] == 0


@pytest.mark.parametrize(
    ("text", "command", "buffered"),
    [
        ("主播好可爱", None, 1),  # 非命令照常进决策链
        ("/come", {"enabled": False}, 1),  # 关闭时命令文本当普通弹幕
        ("/come", {"mappings": {}}, 1),  # 默认开启但白名单为空：分支不激活
        ("/admin all", None, 0),  # 白名单外静默丢弃
        ("/sleep", {"mappings": {"come": "到主播身边来"}}, 0),  # 配置即白名单
    ],
)
async def test_non_delegating_paths(text: str, command, buffered: int) -> None:
    agent, registry = _agent(command)
    await agent.handle_message(_danmaku(text))
    registry.invoke.assert_not_awaited()
    assert agent._buffer.size == buffered


async def test_unregistered_target_is_rejected_with_log(loguru_capture) -> None:
    agent, registry = _agent(accepted=False)
    await agent.handle_message(_danmaku("/come"))
    assert registry.invoke.await_count == 1 and agent._buffer.size == 0
    assert any("受理失败" in r["message"] for r in loguru_capture.records)


def test_command_parsing_is_not_an_llm_tool() -> None:
    """红线：命令解析是代码直连的内部件，不注册工具、不出现工具声明。"""
    registry = ToolRegistry()
    agent, _ = _agent(registry=registry)
    agent._register_tools()
    names = [spec.name for spec in registry.list_tools()]
    assert len(names) == 2 and not any("command" in name for name in names)

    for py_file in Path("src/agents/streamer/command").glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        assert not any(word in source for word in ("ToolSpec", "ToolRegistry", "register_tool_provider")), py_file
