"""CommandRouter：非命令放行、白名单外静默丢弃、限频、委派契约参数、受理失败与注册表缺失"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.command.router import CommandRouter
from src.agents.streamer.config import StreamerCommandConfig
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.utils.time_utils import now_ms
from src.modules.tools.models import ToolExecutionResult


def _router(registry, **overrides) -> CommandRouter:
    fields = {"enabled": True, "mappings": {"come": "到主播身边来", "sleep": "回床睡觉"}, **overrides}
    return CommandRouter(StreamerCommandConfig.from_dict(fields), registry)


def _danmaku(content: str, user_id: str = "u1") -> RoomMessagePayload:
    user = RoomMessageUser(id=user_id, name="测试观众")
    return RoomMessagePayload(message_type="danmaku", user=user, content=content, timestamp_ms=now_ms())


def _registry(success: bool = True) -> MagicMock:
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="framework_delegate",
            success=success,
            structured_content={"accepted": True, "task_id": "deleg_1", "executor": "minecraft"} if success else None,
            error_message="" if success else "agent 'minecraft' not found",
        )
    )
    return registry


@pytest.mark.parametrize(
    ("text", "consumed", "invoked"),
    [("主播好可爱", False, 0), ("/admin all", True, 0), ("/sleep", True, 1)],
    ids=["not_command", "out_of_whitelist", "whitelisted"],
)
async def test_dispatch_branches(text: str, consumed: bool, invoked: int) -> None:
    registry = _registry()
    assert await _router(registry).try_dispatch(_danmaku(text)) is consumed
    assert registry.invoke.await_count == invoked


@pytest.mark.parametrize(("target", "agent"), [(None, ""), ("text_adv", "text_adv")])
async def test_delegation_contract_arguments(target, agent: str) -> None:
    """目标未配置时留空，由框架委派原语解析为当前唯一启用的游戏 Agent；显式配置原样透传。"""
    registry = _registry()
    overrides = {"target_agent": target} if target else {}
    await _router(registry, **overrides).try_dispatch(_danmaku("/sleep"))

    invocation = registry.invoke.call_args.args[0]
    assert invocation.tool_name == "framework_delegate" and invocation.source == "streamer"
    assert invocation.arguments == {"agent": agent, "instruction": "回床睡觉"}


async def test_rate_limit_is_per_user() -> None:
    registry = _registry()
    router = _router(registry, rate_max=2)
    for _ in range(3):
        assert await router.try_dispatch(_danmaku("/come", user_id="u1")) is True
    assert registry.invoke.await_count == 2
    await router.try_dispatch(_danmaku("/come", user_id="u2"))
    assert registry.invoke.await_count == 3


@pytest.mark.parametrize("registry", [_registry(success=False), None], ids=["accept_failed", "no_registry"])
async def test_command_consumed_even_when_delegation_impossible(registry) -> None:
    """受理失败或注册表缺失：不抛异常，消息仍被消费（不进决策链）。"""
    assert await _router(registry).try_dispatch(_danmaku("/come")) is True


# =============================================================================
# 工具动作：遥控型游戏的命令直接变成一次固定工具调用
# =============================================================================

_TOOL_MAPPINGS = [
    {"command": "up", "tool": "game_2048_press", "arguments": {"key": "up"}},
    {"command": "restart", "tool": "game_2048_press", "arguments": {"key": "restart"}},
]


def _tool_registry(success: bool = True) -> MagicMock:
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="game_2048_press", success=success, error_message=None if success else "终局后不能按"
        )
    )
    return registry


async def test_tool_mapping_invokes_tool_with_fixed_arguments() -> None:
    """命中工具映射：按配置里写死的工具名与参数调用一次，source=streamer；观众参数不透传。"""
    registry = _tool_registry()
    router = _router(registry, mappings={}, tool_mappings=_TOOL_MAPPINGS)
    assert await router.try_dispatch(_danmaku("/up 左 右 随便写")) is True
    registry.invoke.assert_awaited_once()
    invocation = registry.invoke.call_args.args[0]
    assert invocation.tool_name == "game_2048_press"
    assert invocation.arguments == {"key": "up"}
    assert invocation.source == "streamer"


async def test_tool_mapping_wins_over_delegate_mapping_with_same_name() -> None:
    """同名命令两表都配：以工具动作为准，不再走 framework_delegate。"""
    registry = _tool_registry()
    router = _router(registry, mappings={"up": "往上走"}, tool_mappings=_TOOL_MAPPINGS)
    assert await router.try_dispatch(_danmaku("/up")) is True
    assert registry.invoke.call_args.args[0].tool_name == "game_2048_press"


async def test_tool_mapping_failure_still_consumed() -> None:
    """工具调用失败（游戏侧拒绝）：记日志、消息仍被消费、不抛异常、不进决策链。"""
    router = _router(_tool_registry(success=False), mappings={}, tool_mappings=_TOOL_MAPPINGS)
    assert await router.try_dispatch(_danmaku("/restart")) is True


async def test_incomplete_tool_mapping_is_skipped() -> None:
    """命令名或工具名留空的映射不入白名单：命中它的命令静默丢弃。"""
    registry = _tool_registry()
    router = _router(registry, mappings={}, tool_mappings=[{"command": "left", "tool": ""}])
    assert await router.try_dispatch(_danmaku("/left")) is True
    registry.invoke.assert_not_awaited()


async def test_tool_mapping_shares_rate_limit_with_delegate_commands() -> None:
    """限频闸对两类动作一视同仁：同一用户的委派命令与工具命令合计计数。"""
    registry = _tool_registry()
    router = _router(registry, rate_max=2, tool_mappings=_TOOL_MAPPINGS)
    assert await router.try_dispatch(_danmaku("/come")) is True
    assert await router.try_dispatch(_danmaku("/up")) is True
    assert await router.try_dispatch(_danmaku("/up")) is True
    assert registry.invoke.await_count == 2
