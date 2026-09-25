"""MinecraftAgent 配置

只放"该游戏特有"的配置（其它公用依赖经构造器注入，不走配置）。
默认值即可构造——不连真实 Minecraft 世界也可启动（空闲零消耗，无副作用）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.agents.minecraft.builder.config import MinecraftBuilderConfig
from src.modules.config.schemas.base import BaseConfig
from src.modules.mcp.config import McpServerConfig


class MinecraftContextConfig(BaseModel):
    """本玩家的工作上下文预算，字符预算限制呈现体积，不冒充厂商 Token 计数。"""

    max_context_chars: int = Field(default=120_000, ge=24_000, description="触发集中整理的消息与工具声明字符预算")
    recent_turns: int = Field(default=6, ge=1, le=20, description="集中整理时优先保留的近期完整决策轮数")
    summary_max_chars: int = Field(default=6000, ge=1000, le=12000, description="推理摘要的最大字符数")
    observation_inline_chars: int = Field(default=6000, ge=1000, description="大观察展开前的呈现预算，决策证据可超出")
    archive_max_chars: int = Field(
        default=8_000_000, ge=12000, description="当前任务原始观察的字符预算，至少保留最新原文"
    )


class MinecraftConfig(BaseConfig):
    """MinecraftAgent 运行时配置

    Attributes:
        mcp: Agent 私有 MCP server 配置（位置即归属）。enabled=true 时
            _on_start 装配 McpToolProvider 并以逐工具可见名单（
            fail-closed）注册进 ToolRegistry；false 时不装配（Agent 命令
            驱动，MCP 不可用即降级）。
    """

    # 设计任务从属于当前游戏，关闭 Minecraft 时不单独装配建造 Agent。
    context: MinecraftContextConfig = Field(default_factory=MinecraftContextConfig, description="游戏任务上下文预算")
    builder: MinecraftBuilderConfig = Field(default_factory=MinecraftBuilderConfig, description="按需建筑设计 Agent")
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
