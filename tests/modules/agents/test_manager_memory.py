"""
AgentManager memory/tool_registry 回退逻辑测试（Wave 8 / 记忆接线修复）

覆盖：
- ``AgentManager(memory=m)`` → ``self._memory`` 持有
- ``enable_agent(name)`` 不传 ``memory`` → 回退到成员（Dashboard 场景）
- ``enable_agent(name, memory=explicit)`` → 显式值覆盖成员
- ``tool_registry`` 同样的回退逻辑（顺手修复的既存缺陷）
- ``instantiate_agent`` 接受 ``memory`` 关键字（工厂侧透传验证）

通过 ``unittest.mock.patch`` stub ``factory.instantiate_agent``，避免真实构造
``StreamerAgent``（其 ``memory`` 形参由并行代理添加，本任务不依赖）。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import patch

import pytest

from src.modules.agents.base import BaseAgent
from src.modules.agents.manager import AgentManager
from src.modules.tools import ToolRegistry


# =============================================================================
# Fixtures / Helpers
# =============================================================================


class _StubAgent(BaseAgent):
    """最小可启动 Agent（list_tools 返回空；start/stop/cleanup 走 BaseAgent 默认）。"""

    name = "stub_agent"
    description = "AgentManager 测试用 stub Agent"

    def list_tools(self):  # type: ignore[override]
        return []


@pytest.fixture
def stub_factory():
    """Patch ``factory.instantiate_agent`` 返回 ``_StubAgent`` 实例。

    返回 ``(patcher, calls)`` —— ``calls`` 是 ``instantiate_agent`` 的调用参数列表，
    测试可断言 fallback 逻辑。
    """

    calls: list[dict[str, Any]] = []

    def _fake_instantiate(name, config, **kwargs):
        calls.append({"name": name, "config": config, **kwargs})
        return _StubAgent()

    patcher = patch("src.modules.agents.factory.instantiate_agent", side_effect=_fake_instantiate)
    return patcher, calls


@pytest.fixture
async def manager_with_memory(stub_factory) -> AsyncGenerator[tuple[AgentManager, list], None]:
    """构造带 memory + tool_registry 的 AgentManager；patch 工厂以记录调用。"""
    patcher, calls = stub_factory
    memory_obj = object()
    registry = ToolRegistry()
    mgr = AgentManager(tool_registry=registry, memory=memory_obj)
    patcher.start()
    try:
        yield mgr, calls
    finally:
        patcher.stop()


# =============================================================================
# __init__: 成员保存
# =============================================================================


def test_manager_init_stores_memory_member() -> None:
    """``AgentManager(memory=m)`` → ``m`` 持有为 ``_memory``。"""
    mem = object()
    mgr = AgentManager(memory=mem)
    assert mgr._memory is mem


def test_manager_init_memory_default_none() -> None:
    """不传 memory → 成员为 ``None``（不抛错，向后兼容旧调用）。"""
    mgr = AgentManager()
    assert mgr._memory is None


def test_manager_init_stores_tool_registry() -> None:
    """``tool_registry`` 同样被存储（既有路径）。"""
    reg = ToolRegistry()
    mgr = AgentManager(tool_registry=reg)
    assert mgr._tool_registry is reg


# =============================================================================
# enable_agent: memory 回退
# =============================================================================


@pytest.mark.asyncio
async def test_enable_agent_memory_fallback_to_member(manager_with_memory) -> None:
    """Dashboard 场景：``enable_agent`` 不传 ``memory`` → 工厂收到 ``self._memory``。"""
    mgr, calls = manager_with_memory
    memory_obj = mgr._memory  # 成员

    ok = await mgr.enable_agent(
        "streamer",
        config={},
        llm_manager=None,
        prompt_manager=None,
        event_bus=None,
    )
    assert ok is True
    assert len(calls) == 1
    # 工厂被调用时 memory 参数 = 成员对象（fallback 生效）
    assert calls[0]["memory"] is memory_obj


@pytest.mark.asyncio
async def test_enable_agent_memory_explicit_overrides_member(manager_with_memory) -> None:
    """``memory=explicit`` 显式传入 → 工厂收到 explicit，覆盖成员。"""
    mgr, calls = manager_with_memory
    explicit = object()  # 与成员不同的新对象

    await mgr.enable_agent(
        "streamer",
        config={},
        llm_manager=None,
        prompt_manager=None,
        event_bus=None,
        memory=explicit,
    )
    assert calls[0]["memory"] is explicit
    assert calls[0]["memory"] is not mgr._memory


@pytest.mark.asyncio
async def test_enable_agent_tool_registry_fallback_to_member(manager_with_memory) -> None:
    """顺手修复：``tool_registry`` 不传时也回退到成员（旧 dashboard 路径缺陷）。"""
    mgr, calls = manager_with_memory
    registry = mgr._tool_registry

    await mgr.enable_agent(
        "streamer",
        config={},
        llm_manager=None,
        prompt_manager=None,
        event_bus=None,
        # 不传 tool_registry / memory
    )
    assert calls[0]["tool_registry"] is registry


@pytest.mark.asyncio
async def test_enable_agent_tool_registry_explicit_overrides_member(manager_with_memory) -> None:
    """``tool_registry=explicit`` 显式传入 → 覆盖成员。"""
    mgr, calls = manager_with_memory
    explicit_reg = ToolRegistry()

    await mgr.enable_agent(
        "streamer",
        config={},
        llm_manager=None,
        prompt_manager=None,
        event_bus=None,
        tool_registry=explicit_reg,
    )
    assert calls[0]["tool_registry"] is explicit_reg
    assert calls[0]["tool_registry"] is not mgr._tool_registry


@pytest.mark.asyncio
async def test_enable_agent_both_overrides_together(manager_with_memory) -> None:
    """``tool_registry`` 和 ``memory`` 同时显式传入 → 两个都用 explicit。"""
    mgr, calls = manager_with_memory
    explicit_reg = ToolRegistry()
    explicit_mem = object()

    await mgr.enable_agent(
        "streamer",
        config={},
        llm_manager=None,
        prompt_manager=None,
        event_bus=None,
        tool_registry=explicit_reg,
        memory=explicit_mem,
    )
    assert calls[0]["tool_registry"] is explicit_reg
    assert calls[0]["memory"] is explicit_mem


# =============================================================================
# 边界：两个成员都是 None（最坏情况）
# =============================================================================


@pytest.mark.asyncio
async def test_enable_agent_when_manager_has_no_memory_nor_registry(stub_factory) -> None:
    """Manager 构造时既没 memory 也没 registry → enable_agent 不报错；工厂收到 None。"""
    patcher, calls = stub_factory
    patcher.start()
    try:
        mgr = AgentManager()
        await mgr.enable_agent(
            "streamer",
            config={},
            llm_manager=None,
            prompt_manager=None,
            event_bus=None,
        )
        # fallback 后都是 None（不抛错）
        assert calls[0]["memory"] is None
        assert calls[0]["tool_registry"] is None
    finally:
        patcher.stop()


# =============================================================================
# factory.instantiate_agent 接受 memory kwarg（签名 smoke test）
# =============================================================================


@pytest.mark.asyncio
async def test_factory_instantiate_agent_accepts_memory_kwarg() -> None:
    """``instantiate_agent(..., memory=m)`` 签名层接受 memory（透传到 StreamerAgent）。"""
    from src.modules.agents.factory import instantiate_agent

    # 用 inspect 验证签名即可（不实际构造 StreamerAgent，避免依赖并行代理）
    import inspect

    sig = inspect.signature(instantiate_agent)
    assert "memory" in sig.parameters
    # 关键字参数
    assert sig.parameters["memory"].kind == inspect.Parameter.KEYWORD_ONLY
    # 默认 None
    assert sig.parameters["memory"].default is None
