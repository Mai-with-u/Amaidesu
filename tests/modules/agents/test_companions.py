"""Agent 附属采集器：名单、按 Agent 配置构造、随 Agent 登记与停掉。

附属采集器只对所属 Agent 有意义，随它一同开关；这里守住三件事：
- 名单与 Agent 包里构造出的实例名一致（面板与开关拦截只查名单，不构造实例）；
- 配置由 Agent 配置换算（连接取 Agent 的 MCP，那边关着就不装）；
- 登记进采集器管理器与停掉移除的来回。
"""

from __future__ import annotations

import pytest

from src.agents.minecraft.attention_collector import MaicraftAttentionCollector
from src.agents.minecraft.companions import companion_config
from src.modules.agents.companions import (
    COMPANION_COLLECTORS,
    build_companion_collectors,
    companion_names,
    owner_of,
    register_companions,
    stop_companions,
)
from src.modules.collectors.manager import CollectorManager


def test_declared_names_match_built_collectors() -> None:
    """名单里的名字就是 Agent 包构造出来的实例名，一个不多一个不少。"""
    for agent_name, names in COMPANION_COLLECTORS.items():
        built = [collector.name for collector in build_companion_collectors(agent_name, {})]
        assert tuple(built) == names


def test_owner_and_names_lookup() -> None:
    assert companion_names("minecraft") == ("maicraft_attention", "maicraft_chat")
    assert companion_names("streamer") == ()
    assert owner_of("maicraft_chat") == "minecraft"
    assert owner_of("console_input") is None


def test_config_comes_from_the_agent() -> None:
    """连接与 Agent 的 MCP 是同一份（含请求头），等待与重试间隔取 Agent 的两项。"""
    agent_config = {
        "events_wait_ms": 30_000,
        "events_retry_interval_ms": 800,
        "mcp": {"url": "http://10.0.0.5:9000/mcp", "headers": {"Authorization": "Bearer x"}},
    }
    config = companion_config(agent_config)
    assert config is not None
    assert config["wait_ms"] == 30_000
    assert config["retry_interval_ms"] == 800
    collector = MaicraftAttentionCollector(config=config)
    assert collector.typed_config.mcp.url == "http://10.0.0.5:9000/mcp"
    assert collector.typed_config.mcp.headers == {"Authorization": "Bearer x"}


def test_nothing_built_when_agent_mcp_is_off() -> None:
    """Agent 的 MCP 关着时 Agent 本身不接游戏，两条流也不装。"""
    assert build_companion_collectors("minecraft", {"mcp": {"enabled": False}}) == []


def test_agent_without_companions_builds_nothing() -> None:
    assert build_companion_collectors("streamer", {}) == []


@pytest.mark.asyncio
async def test_register_then_stop_round_trip() -> None:
    """启动装配时登记（start_all 统一启动）；Agent 停用时停掉并移出管理器。"""
    manager = CollectorManager()
    registered = register_companions(manager, "minecraft", {})
    assert registered == ["maicraft_attention", "maicraft_chat"]
    assert set(manager.list_collectors()) == {"maicraft_attention", "maicraft_chat"}
    assert "随 Agent minecraft 开关" in manager.descriptions["maicraft_chat"]

    # 重复登记不会多出第二份
    assert register_companions(manager, "minecraft", {}) == []

    stopped = await stop_companions(manager, "minecraft")
    assert stopped == ["maicraft_attention", "maicraft_chat"]
    assert manager.list_collectors() == []


def test_startup_assembly_follows_agent_enablement() -> None:
    """启动装配只给启用的 Agent 装附属采集器：只开主播时一个都不装，开了 Minecraft 才装上两条流。"""
    # 函数内 import：main 是入口模块，导入会拉起整套装配依赖，只有这个用例需要
    from main import _enabled_agents_with_companions, _register_companion_collectors

    manager = CollectorManager()
    agents = {"enabled": ["streamer"], "minecraft": {}}
    assert _enabled_agents_with_companions(agents) == []
    _register_companion_collectors(manager, agents)
    assert manager.list_collectors() == []

    agents["enabled"] = ["streamer", "minecraft"]
    assert _enabled_agents_with_companions(agents) == ["minecraft"]
    _register_companion_collectors(manager, agents)
    assert set(manager.list_collectors()) == {"maicraft_attention", "maicraft_chat"}
