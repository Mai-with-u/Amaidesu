"""CommandRouter 单元测试：安全闸与委派全分支。

端到端行为（命令不进决策缓冲等）由 ``test_command_wiring`` /
``test_command_e2e`` 经 Agent 公共入口覆盖；本文件直接打 router，
覆盖四类分支的边界：白名单外 / 限频 / 委派成功 / ToolRegistry 缺失。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.command.router import CommandRouter
from src.agents.streamer.config import StreamerCommandConfig
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.tools.models import ToolExecutionResult
from src.modules.time_utils import now_ms

_MAPPINGS = {"come": "到主播身边来", "sleep": "回床睡觉"}


def _make_config(**overrides) -> StreamerCommandConfig:
    fields = {"enabled": True, "mappings": dict(_MAPPINGS)}
    fields.update(overrides)
    return StreamerCommandConfig.from_dict(fields)


def _make_router(registry: MagicMock | None, **config_overrides) -> CommandRouter:
    return CommandRouter(_make_config(**config_overrides), registry)


def _make_danmaku(content: str, user_id: str = "u1") -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id=user_id, name="测试观众"),
        content=content,
        timestamp_ms=now_ms(),
    )


def _ok_registry() -> MagicMock:
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="framework_delegate",
            success=True,
            structured_content={"accepted": True, "task_id": "deleg_1", "executor": "minecraft"},
        )
    )
    return registry


# =============================================================================
# 四类分支
# =============================================================================


@pytest.mark.asyncio
async def test_non_command_text_not_consumed() -> None:
    """非命令文本返回 False，调用方继续进决策链。"""
    router = _make_router(_ok_registry())
    assert await router.try_dispatch(_make_danmaku("主播好可爱")) is False


@pytest.mark.asyncio
async def test_out_of_whitelist_silently_dropped() -> None:
    """白名单外命令静默丢弃：消费消息但不触发 invoke。"""
    registry = _ok_registry()
    router = _make_router(registry)
    assert await router.try_dispatch(_make_danmaku("/admin all")) is True
    registry.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_drops_excess_commands() -> None:
    """同一用户窗口内超过 rate_max 条后，后续命令被丢弃（不 invoke）。"""
    registry = _ok_registry()
    router = _make_router(registry, rate_max=2)
    assert await router.try_dispatch(_make_danmaku("/come", user_id="u1")) is True
    assert await router.try_dispatch(_make_danmaku("/come", user_id="u1")) is True
    assert registry.invoke.await_count == 2
    # 第三条同用户命令被限频丢弃
    assert await router.try_dispatch(_make_danmaku("/come", user_id="u1")) is True
    assert registry.invoke.await_count == 2
    # 其他用户不受影响
    assert await router.try_dispatch(_make_danmaku("/come", user_id="u2")) is True
    assert registry.invoke.await_count == 3


@pytest.mark.asyncio
async def test_whitelisted_command_delegates_with_contract_arguments() -> None:
    """命中白名单：framework_delegate 收到 agent/instruction 契约参数与 source=streamer。

    目标未配置时留空——由框架委派原语解析为当前唯一启用的游戏 Agent，
    主播侧不写死任何游戏名。
    """
    registry = _ok_registry()
    router = _make_router(registry)
    assert await router.try_dispatch(_make_danmaku("/sleep")) is True
    registry.invoke.assert_awaited_once()
    invocation = registry.invoke.call_args.args[0]
    assert invocation.tool_name == "framework_delegate"
    assert invocation.arguments == {"agent": "", "instruction": "回床睡觉"}
    assert invocation.source == "streamer"


@pytest.mark.asyncio
async def test_configured_target_passed_through() -> None:
    """显式配置的目标注册名原样透传（多游戏并存时点名用）。"""
    registry = _ok_registry()
    router = _make_router(registry, target_agent="text_adv")
    assert await router.try_dispatch(_make_danmaku("/sleep")) is True
    assert registry.invoke.call_args.args[0].arguments == {
        "agent": "text_adv",
        "instruction": "回床睡觉",
    }


@pytest.mark.asyncio
async def test_delegate_accept_failure_still_consumed() -> None:
    """委派受理失败（目标不在名册）：拒绝执行、消息仍被消费、不抛异常。"""
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="framework_delegate",
            success=False,
            error_message="agent 'minecraft' not found",
        )
    )
    router = _make_router(registry)
    assert await router.try_dispatch(_make_danmaku("/come")) is True


@pytest.mark.asyncio
async def test_missing_registry_warns_and_consumes() -> None:
    """ToolRegistry 缺失：告警、不委派、消息被消费（不进决策链）。"""
    router = _make_router(None)
    assert await router.try_dispatch(_make_danmaku("/come")) is True
