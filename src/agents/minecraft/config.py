"""MinecraftAgent 配置

只放"该游戏特有"的配置（其它公用依赖经构造器注入，不走配置）。
默认值即可构造——不连真实 Minecraft 世界也可启动（空闲零消耗，无副作用）。
"""

from __future__ import annotations

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.mcp.config import McpServerConfig


class MinecraftConfig(BaseConfig):
    """MinecraftAgent 运行时配置

    Attributes:
        max_steps: 单任务内 ReAct 循环（LLM 推理步数）上限——防失控挂起
        execute_poll_interval_ms: handoff 周期兜底核实间隔（毫秒）——
            订阅通知是提示（可丢/断连），到点主动 task get 核实一次
        execute_wait_timeout_ms: 后台任务单轮 wait_timeout 上限（毫秒）——
            长期无进展注入告警消息（不杀任务），LLM 自行决定后续
        mcp: Agent 私有 MCP server 配置（位置即归属）。enabled=true 时
            _on_start 装配 McpToolProvider 并以逐工具可见名单（ADR-012，
            fail-closed）注册进 ToolRegistry；false 时不装配（Agent 命令
            驱动，MCP 不可用即降级）。
    """

    max_steps: int = Field(default=50, ge=1, description="单任务 ReAct 循环最大步数（超出挂起上报）")
    execute_poll_interval_ms: int = Field(
        default=2000,
        ge=100,
        description="handoff 周期兜底核实任务快照的间隔（毫秒）",
    )
    execute_wait_timeout_ms: int = Field(
        default=1_800_000,
        ge=1000,
        description="后台任务单轮 wait_timeout 上限（毫秒，到点注入告警不杀任务）",
    )
    mcp: McpServerConfig = Field(
        default_factory=lambda: McpServerConfig(url="http://127.0.0.1:8766/mcp"),
        description="Agent 私有 MCP server（enabled=false 时不装配）",
    )


__all__ = ["MinecraftConfig"]
