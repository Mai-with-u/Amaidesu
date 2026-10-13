"""MinecraftAgent 配置

只放"该游戏特有"的配置（其它公用依赖经构造器注入，不走配置）。
默认值即可构造——不连真实 Minecraft 世界也可启动（空闲零消耗，无副作用）。
"""

from __future__ import annotations

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.context import ContextBudgetConfig
from src.modules.mcp.config import McpServerConfig


class MinecraftContextConfig(ContextBudgetConfig):
    """本玩家的工作上下文预算：按模型窗口比例换算 token 预算，历史涨到预算时集中整理。

    实测动作步前缀缓存命中 79%–96%，多带上下文的代价远小于一次 20–150 秒的摘要：
    旧版 16 万字符（约 55k token）的预算让角色每一分半钟就停下来整理一次，
    所以默认给到窗口的一半、上限 20 万 token。
    """

    window_ratio: float = Field(default=0.5, gt=0.0, le=1.0, title="窗口占比", description="预算占模型上下文窗口的比例")
    max_tokens: int = Field(
        default=200_000, ge=4_000, title="预算上限", description="触发集中整理的 token 上限（本地分词估算口径）"
    )
    prepare_ratio: float = Field(
        default=0.7,
        gt=0.0,
        le=1.0,
        title="提前整理线",
        description="让出等待游戏结果时，历史超过预算的这一比例就借空档在后台提前整理",
    )
    target_ratio: float = Field(
        default=0.6, gt=0.0, lt=1.0, title="整理后回落线", description="集中整理把历史压回到预算的这一比例"
    )
    recent_turns: int = Field(
        default=6,
        ge=1,
        le=20,
        title="保留近期轮数",
        description="集中整理时优先原样保留的最近几轮决策记录",
        json_schema_extra={"x-ui-advanced": True},
    )
    summary_max_chars: int = Field(
        default=6000,
        ge=1000,
        le=12000,
        title="摘要长度上限",
        description="整理出的历史摘要的最大字符数",
        json_schema_extra={"x-ui-advanced": True},
    )


class MinecraftConfig(BaseConfig):
    """MinecraftAgent 运行时配置

    Attributes:
        mcp: Agent 私有 MCP server 配置（位置即归属）。enabled=true 时
            _on_start 装配 McpToolProvider 并以逐工具可见名单（
            fail-closed）注册进 ToolRegistry；false 时不装配（Agent 命令
            驱动，MCP 不可用即降级）。
        events_wait_ms: 一次读事件流最多等多久；没有新事件就空手返回再等下一次。跟踪后台目标的读取
            与两个附属采集器（身体事件流、聊天流）共用。
        events_retry_interval_ms: 附属采集器连不上 Mod 或读失败后，隔多久重试（按倍数退避，最长 60 秒）。
    """

    context: MinecraftContextConfig = Field(
        default_factory=MinecraftContextConfig,
        title="上下文预算",
        description="游戏任务时带给模型的上下文体积预算（超出部分整理成摘要）",
    )
    events_wait_ms: int = Field(
        default=25_000,
        ge=1000,
        le=55_000,
        title="事件等待",
        description="一次读游戏事件流最多等多久（毫秒；跟踪后台目标与附属采集器共用，MaiCraft 上限 60 秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    events_retry_interval_ms: int = Field(
        default=5000,
        ge=200,
        title="事件重试间隔",
        description="身体事件流、聊天流连不上 Mod 或读失败后的重试间隔（毫秒，逐次加长到最长 60 秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    mcp: McpServerConfig = Field(
        default_factory=lambda: McpServerConfig(url="http://127.0.0.1:8766/mcp"),
        title="MCP 连接",
        description="连接游戏 Mod 的 MCP 服务地址（关闭后 Agent 降级运行）",
    )


__all__ = ["MinecraftConfig", "MinecraftContextConfig"]
