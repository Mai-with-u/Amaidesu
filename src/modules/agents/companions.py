"""Agent 附属采集器：只对某个 Agent 有意义、随它一同开关的采集器

采集器分两类：

- **通用采集器**：直播间与操作者的输入（B 站弹幕、控制台、语音转写），谁都能用，
  在 ``collectors.toml`` 的 ``enabled`` 名单里单独开关，装配走采集器工厂。
- **Agent 附属采集器**：某个 Agent 所接外部世界的持续流（Minecraft 的身体事件流、游戏聊天流）。
  离开那个 Agent 就没有意义——Agent 没开时它们只会对着连不上的游戏一直重试——所以不进
  ``collectors.toml``，Agent 启用就装上、停用就停掉；配置由所属 Agent 的配置换算，不另抄一份。

附属采集器仍是普通采集器（持续流、自产自发事件、登记在 CollectorManager 里，面板照样看得到运行状态），
只是"装配期"从采集器名单换成了所属 Agent 的启用。开关只认"Agent 在不在启用名单里"：Agent 重启、心跳自愈
走的是重建，附属采集器不跟着停——它们每次重新接入只对齐游标、不补发，跟着重建就会漏掉重建期间的事。

本模块是框架侧的唯一入口：哪个 Agent 带哪几个附属采集器（名单不需要构造实例就能查，面板与开关拦截用），
以及按 Agent 名分发到 Agent 包里的构造函数。新 Agent 带附属采集器时在这里加一行名单与一个分支，
构造与配置换算留在 Agent 自己的包里。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.modules.collectors.base import BaseCollector
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.collectors.manager import CollectorManager

logger = get_logger("AgentCompanions")

#: Agent 注册名 → 它的附属采集器注册名（与 Agent 包里构造出的实例 name 一致，测试守住）
COMPANION_COLLECTORS: Dict[str, tuple[str, ...]] = {
    "minecraft": ("maicraft_attention", "maicraft_chat"),
}


def companion_names(agent_name: str) -> tuple[str, ...]:
    """这个 Agent 带哪几个附属采集器；不带为空。"""
    return COMPANION_COLLECTORS.get(agent_name, ())


def owner_of(collector_name: str) -> Optional[str]:
    """这个采集器随哪个 Agent 开关；通用采集器为 None。"""
    for agent_name, names in COMPANION_COLLECTORS.items():
        if collector_name in names:
            return agent_name
    return None


def build_companion_collectors(
    agent_name: str,
    agent_config: Optional[Dict[str, Any]],
    event_bus: Any = None,
) -> List[BaseCollector]:
    """按 Agent 的配置构造它的附属采集器（未启动、未登记）；Agent 不带或此刻不该带时为空表。"""
    if agent_name == "minecraft":
        # 函数内 import：Minecraft 包拉起 MCP 客户端等依赖，只有启用了 Minecraft Agent 才需要
        from src.agents.minecraft.companions import build_companion_collectors as build_minecraft

        return build_minecraft(agent_config or {}, event_bus)
    return []


def register_companions(
    manager: "CollectorManager",
    agent_name: str,
    agent_config: Optional[Dict[str, Any]],
    event_bus: Any = None,
) -> List[str]:
    """启动装配用：把 Agent 的附属采集器登记进管理器（随后由 ``start_all`` 一并启动），返回登记了哪几个。"""
    registered: List[str] = []
    for collector in build_companion_collectors(agent_name, agent_config, event_bus):
        if manager.register(collector, description=f"{collector.description}（随 Agent {agent_name} 开关）"):
            registered.append(collector.name)
    if registered:
        logger.info(f"Agent '{agent_name}' 的附属采集器已登记：{registered}")
    return registered


async def start_companions(
    manager: "CollectorManager",
    agent_name: str,
    agent_config: Optional[Dict[str, Any]],
    event_bus: Any = None,
) -> List[str]:
    """运行中启用 Agent 时：登记并启动它的附属采集器；已在运行的不重复启动。返回启动了哪几个。"""
    started: List[str] = []
    for collector in build_companion_collectors(agent_name, agent_config, event_bus):
        if manager.get_collector_by_name(collector.name) is None:
            manager.register(collector, description=f"{collector.description}（随 Agent {agent_name} 开关）")
        if await manager.start_collector(collector.name):
            started.append(collector.name)
        else:
            logger.warning(f"Agent '{agent_name}' 的附属采集器 '{collector.name}' 启动失败")
    return started


async def stop_companions(manager: "CollectorManager", agent_name: str) -> List[str]:
    """运行中停用 Agent 时：停掉并移除它的附属采集器。返回停掉了哪几个。"""
    stopped: List[str] = []
    for name in companion_names(agent_name):
        if manager.get_collector_by_name(name) is None:
            continue
        if await manager.disable_collector(name):
            stopped.append(name)
        else:
            logger.warning(f"Agent '{agent_name}' 的附属采集器 '{name}' 停止失败")
    return stopped


__all__ = [
    "COMPANION_COLLECTORS",
    "build_companion_collectors",
    "companion_names",
    "owner_of",
    "register_companions",
    "start_companions",
    "stop_companions",
]
