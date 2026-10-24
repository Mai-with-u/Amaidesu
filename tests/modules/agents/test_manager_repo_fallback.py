"""
AgentManager 仓储 / tool_registry 回退逻辑测试。

覆盖：
- ``AgentManager(viewer_profile_repo=..., recap_repo=...)`` 成员持有
- ``enable_agent(name)`` 不传仓储 → 回退到成员（Dashboard 动态启用场景）
- ``enable_agent(name, deps=AgentDeps(recap_repo=explicit))`` → 显式值覆盖成员
- ``tool_registry`` 同样的回退逻辑
- ``instantiate_agent`` 签名接受主播记忆两仓储与游戏事件仓储

通过 ``unittest.mock.patch`` stub ``factory.instantiate_agent``，避免真实构造
``StreamerAgent``。
"""

from __future__ import annotations

import inspect
from typing import Any, AsyncGenerator
from unittest.mock import patch

import pytest

from src.modules.agents.base import BaseAgent
from src.modules.agents.manager import AgentDeps, AgentManager
from src.modules.tools import ToolRegistry


class _StubAgent(BaseAgent):
    """最小可启动 Agent（list_tools 返回空；start/stop/cleanup 走 BaseAgent 默认）。"""

    name = "stub_agent"
    description = "AgentManager 测试用 stub Agent"

    def list_tools(self):  # type: ignore[override]
        return []


@pytest.fixture
def stub_factory():
    """Patch ``factory.instantiate_agent`` 返回 ``_StubAgent``；``calls`` 记录工厂收到的参数。"""
    calls: list[dict[str, Any]] = []

    def _fake_instantiate(name, config, **kwargs):
        calls.append({"name": name, "config": config, **kwargs})
        return _StubAgent()

    patcher = patch("src.modules.agents.factory.instantiate_agent", side_effect=_fake_instantiate)
    return patcher, calls


@pytest.fixture
async def manager_with_repos(stub_factory) -> AsyncGenerator[tuple[AgentManager, list], None]:
    """构造带记忆仓储 + tool_registry 的 AgentManager；patch 工厂以记录调用。"""
    patcher, calls = stub_factory
    mgr = AgentManager(
        tool_registry=ToolRegistry(),
        viewer_profile_repo=object(),
        recap_repo=object(),
        event_repo=object(),
    )
    patcher.start()
    try:
        yield mgr, calls
    finally:
        patcher.stop()


async def _enable(mgr: AgentManager, **kwargs: Any) -> bool:
    return await mgr.enable_agent(
        "streamer", config={}, deps=AgentDeps(llm_manager=None, prompt_manager=None, event_bus=None, **kwargs)
    )


def test_manager_init_stores_repo_members() -> None:
    profile_repo, recap_repo = object(), object()
    mgr = AgentManager(viewer_profile_repo=profile_repo, recap_repo=recap_repo)
    assert mgr._viewer_profile_repo is profile_repo
    assert mgr._recap_repo is recap_repo
    assert AgentManager()._recap_repo is None


@pytest.mark.asyncio
async def test_enable_agent_repos_fallback_to_members(manager_with_repos) -> None:
    """Dashboard 场景：不传记忆仓储 → 工厂收到成员对象。"""
    mgr, calls = manager_with_repos
    assert await _enable(mgr) is True
    resolved = calls[0]["deps"]
    assert resolved.viewer_profile_repo is mgr._viewer_profile_repo
    assert resolved.recap_repo is mgr._recap_repo
    assert resolved.event_repo is mgr._event_repo
    assert resolved.tool_registry is mgr._tool_registry


@pytest.mark.asyncio
async def test_enable_agent_explicit_overrides_members(manager_with_repos) -> None:
    mgr, calls = manager_with_repos
    explicit_recap, explicit_reg = object(), ToolRegistry()
    await _enable(mgr, recap_repo=explicit_recap, tool_registry=explicit_reg)
    resolved = calls[0]["deps"]
    assert resolved.recap_repo is explicit_recap
    assert resolved.tool_registry is explicit_reg
    # 未显式传的仍回退成员
    assert resolved.viewer_profile_repo is mgr._viewer_profile_repo


@pytest.mark.asyncio
async def test_enable_agent_without_members_passes_none(stub_factory) -> None:
    """Manager 未注入任何仓储 → enable_agent 不报错；工厂收到 None（Agent 自行降级）。"""
    patcher, calls = stub_factory
    patcher.start()
    try:
        await _enable(AgentManager())
        resolved = calls[0]["deps"]
        assert resolved.recap_repo is None
        assert resolved.viewer_profile_repo is None
        assert resolved.tool_registry is None
    finally:
        patcher.stop()


def test_factory_signature_takes_single_deps_value_object() -> None:
    """``instantiate_agent`` 的基建签名收敛为单一 ``deps`` 值对象；记忆两仓储与游戏事件仓储在 AgentDeps 字段里。"""
    from src.modules.agents.deps import AgentDeps
    from src.modules.agents.factory import instantiate_agent

    params = inspect.signature(instantiate_agent).parameters
    assert list(params) == ["name", "config", "deps"]
    assert params["deps"].kind == inspect.Parameter.KEYWORD_ONLY
    fields = AgentDeps.__dataclass_fields__
    for name in ("viewer_profile_repo", "recap_repo", "event_repo"):
        assert name in fields and fields[name].default is None
    assert "memory" not in params
    assert "memory_policy" not in params
