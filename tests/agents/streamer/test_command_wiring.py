"""观众命令接线测试（最小接线：接入 + 安全闸 + 委派）。

覆盖需求：
- /come、/sleep → framework_delegate invoke（目标留空交给框架解析、instruction=映射
  语义目标，source=streamer），命令消息不进决策缓冲
- 白名单外命令（/admin）静默丢弃，不触发 invoke
- 同一用户窗口内连发超阈值被限频拒绝
- minecraft 未注册（delegate 受理失败）时拒绝执行、记日志、不崩
- 命令解析不注册为 LLM 工具（断言 + 源码 grep 双保险）
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.tools.models import ToolExecutionResult, ToolInvocation
from src.modules.time_utils import now_ms

_COMMAND_MAPPINGS = {"come": "到主播身边来", "sleep": "回床睡觉"}


def _build_agent(
    command_overrides: Optional[dict] = None,
    tool_registry: Optional[MagicMock] = None,
) -> tuple[StreamerAgent, MagicMock]:
    """构造带命令接线的最小 StreamerAgent + mock ToolRegistry（返回两者）。"""
    llm = MagicMock()
    llm.call_tools = AsyncMock()
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    command_section = {"enabled": True, "mappings": dict(_COMMAND_MAPPINGS)}
    if command_overrides:
        command_section.update(command_overrides)
    config = StreamerConfig.from_dict(
        {
            "proactive": {"enabled": False},
            "word_filter": {"enabled": False},
            "batch": {"batch_window_ms": 100, "tick_interval_ms": 50},
            "command": command_section,
        }
    )
    registry = tool_registry if tool_registry is not None else MagicMock()
    if not isinstance(registry.invoke, AsyncMock):
        registry.invoke = AsyncMock(
            return_value=ToolExecutionResult(
                tool_name="framework_delegate",
                success=True,
                structured_content={"accepted": True, "task_id": "deleg_1", "executor": "minecraft"},
            )
        )
    agent = StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        tool_registry=registry,
    )
    return agent, registry


def _make_danmaku(content: str, user_id: str = "u1") -> object:
    from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser

    return RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id=user_id, name="测试观众"),
        content=content,
        timestamp_ms=now_ms(),
    )


def _last_invocation(registry: MagicMock) -> ToolInvocation:
    args, kwargs = registry.invoke.call_args
    return args[0] if args else kwargs["invocation"]


# =============================================================================
# happy 路径：白名单命令 → framework_delegate
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_instruction"),
    [
        ("/come", "到主播身边来"),
        ("/sleep", "回床睡觉"),
    ],
)
async def test_whitelisted_command_delegates(text: str, expected_instruction: str) -> None:
    """白名单命令触发委派：agent/instruction/source 均按契约，消息不进缓冲。"""
    agent, registry = _build_agent()
    await agent.handle_message(_make_danmaku(text))  # type: ignore[arg-type]

    registry.invoke.assert_awaited_once()
    invocation = _last_invocation(registry)
    assert invocation.tool_name == "framework_delegate"
    assert invocation.arguments == {"agent": "", "instruction": expected_instruction}
    assert invocation.source == "streamer"
    # 命令被消费：不进决策缓冲、不进弹幕计数
    assert agent._buffer.size == 0
    assert agent.get_statistics()["total_messages"] == 0


@pytest.mark.asyncio
async def test_non_command_message_keeps_original_path() -> None:
    """非命令文本不触发 invoke，按普通弹幕继续走决策链（行为不变）。"""
    agent, registry = _build_agent()
    await agent.handle_message(_make_danmaku("主播好可爱"))  # type: ignore[arg-type]

    registry.invoke.assert_not_awaited()
    assert agent._buffer.size == 1
    assert agent.get_statistics()["total_messages"] == 1


@pytest.mark.asyncio
async def test_disabled_command_branch_inert() -> None:
    """enabled=false 时整条命令分支不激活：命令文本当普通弹幕处理。"""
    agent, registry = _build_agent(command_overrides={"enabled": False})
    await agent.handle_message(_make_danmaku("/come"))  # type: ignore[arg-type]

    registry.invoke.assert_not_awaited()
    assert agent._buffer.size == 1


# =============================================================================
# 配置驱动性：mappings 白名单变化即时决定命令可用性
# =============================================================================


@pytest.mark.asyncio
async def test_removed_mapping_disables_command() -> None:
    """配置只含 /come 时，/sleep 因不在白名单被静默丢弃。"""
    agent, registry = _build_agent(command_overrides={"mappings": {"come": "到主播身边来"}})
    await agent.handle_message(_make_danmaku("/sleep"))  # type: ignore[arg-type]

    registry.invoke.assert_not_awaited()
    assert agent._buffer.size == 0


@pytest.mark.asyncio
async def test_restored_mapping_reenables_command() -> None:
    """恢复 /sleep 映射后命令恢复委派（配置即白名单，无其他开关干预）。"""
    agent, registry = _build_agent()
    await agent.handle_message(_make_danmaku("/sleep"))  # type: ignore[arg-type]

    registry.invoke.assert_awaited_once()
    assert _last_invocation(registry).arguments == {"agent": "", "instruction": "回床睡觉"}


@pytest.mark.asyncio
async def test_config_default_enabled_with_empty_mappings_inert() -> None:
    """默认 enabled=true：机制默认开放，但 mappings 为空时分支仍不激活。"""
    agent, registry = _build_agent(command_overrides={"mappings": {}})
    await agent.handle_message(_make_danmaku("/come"))  # type: ignore[arg-type]

    registry.invoke.assert_not_awaited()
    assert agent._buffer.size == 1


# =============================================================================
# 拒绝路径：白名单外 / 限频 / 目标未启用
# =============================================================================


@pytest.mark.asyncio
async def test_unwhitelisted_command_dropped_silently() -> None:
    """白名单外命令静默丢弃：不 invoke、不进缓冲。"""
    agent, registry = _build_agent()
    await agent.handle_message(_make_danmaku("/admin all"))  # type: ignore[arg-type]

    registry.invoke.assert_not_awaited()
    assert agent._buffer.size == 0
    assert agent.get_statistics()["total_messages"] == 0


@pytest.mark.asyncio
async def test_rate_limit_rejects_burst() -> None:
    """同一用户窗口内连发超过 rate_max 条，超出部分被限频拒绝。"""
    agent, registry = _build_agent(command_overrides={"rate_max": 2, "rate_window_ms": 60_000})
    for _ in range(2):
        await agent.handle_message(_make_danmaku("/come", user_id="u1"))  # type: ignore[arg-type]
    assert registry.invoke.await_count == 2
    # 第 3 条超限：静默丢弃
    await agent.handle_message(_make_danmaku("/sleep", user_id="u1"))  # type: ignore[arg-type]
    assert registry.invoke.await_count == 2
    assert agent._buffer.size == 0
    # 其他用户不受影响
    await agent.handle_message(_make_danmaku("/come", user_id="u2"))  # type: ignore[arg-type]
    assert registry.invoke.await_count == 3


@pytest.mark.asyncio
async def test_unregistered_target_rejected_with_log(loguru_capture) -> None:
    """目标 Agent 不在名册（delegate 受理失败）：拒绝执行、记日志、不崩。"""
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="framework_delegate",
            success=False,
            error_message="受理失败：目标 Agent 'minecraft' 不在名册",
        )
    )
    with loguru_capture as cap:
        agent, reg = _build_agent(tool_registry=registry)
    await agent.handle_message(_make_danmaku("/come"))  # type: ignore[arg-type]

    assert reg.invoke.await_count == 1
    assert agent._buffer.size == 0
    assert any("受理失败" in r["message"] for r in cap.records), "委派受理失败应记日志"


# =============================================================================
# 红线：命令解析不注册为 LLM 工具
# =============================================================================


def test_command_primitives_not_registered_as_tools() -> None:
    """断言保险：注册后 ToolRegistry 中不出现命令解析条目。"""
    from src.modules.tools.registry import ToolRegistry

    registry = ToolRegistry()
    agent, _ = _build_agent(tool_registry=registry)
    agent._register_tools()
    names = [spec.name for spec in registry.list_tools()]
    assert "parse_command" not in names
    assert not any("command" in name for name in names)
    # 命令接线不新增工具面：streamer 侧仍只有既有两个注册项
    assert len(names) == 2


def test_command_package_has_no_tool_spec_grep() -> None:
    """grep 保险：command/ 包源码不出现 ToolSpec / ToolRegistry 字样。"""
    pkg_dir = Path("src/agents/streamer/command")
    for py_file in pkg_dir.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        assert "ToolSpec" not in source, py_file
        assert "ToolRegistry" not in source, py_file
        assert "register_tool_provider" not in source, py_file
