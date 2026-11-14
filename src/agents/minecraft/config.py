"""MinecraftAgent 配置

只放"该游戏特有"的配置（其它公用依赖经构造器注入，不走配置）。
默认值即可构造——不连真实 Minecraft 世界也可启动（空闲零消耗，无副作用）。
"""

from __future__ import annotations

from pydantic import Field, model_validator

from src.modules.config.schemas.base import BaseConfig
from src.modules.context import ContextBudgetConfig
from src.modules.mcp.config import McpServerConfig

#: 游戏 Mod MCP 服务的默认端点（MaiCraft 本机默认端口 8766）
DEFAULT_MAICRAFT_MCP_URL = "http://127.0.0.1:8766/mcp"


class MinecraftContextConfig(ContextBudgetConfig):
    """本玩家的工作上下文预算：按模型窗口比例换算 token 预算，历史涨到预算时集中整理。

    实测动作步前缀缓存命中 79%–96%，多带上下文的代价远小于一次 20–150 秒的摘要；
    16 万字符（约 55k token）的预算会让角色每一分半钟就停下来整理一次，
    所以默认给到窗口的一半、上限 20 万 token。
    """

    window_ratio: float = Field(
        default=0.5,
        gt=0.0,
        le=1.0,
        title="窗口占比",
        description="预算占模型上下文窗口的比例",
    )
    max_tokens: int = Field(
        default=200_000,
        ge=4_000,
        title="预算上限",
        description="触发集中整理的 token 上限（本地分词估算口径）",
    )
    prepare_ratio: float = Field(
        default=0.7,
        gt=0.0,
        le=1.0,
        title="提前整理线",
        description="让出等待游戏结果时，历史超过预算的这一比例就借空档在后台提前整理",
    )
    target_ratio: float = Field(
        default=0.6,
        gt=0.0,
        lt=1.0,
        title="整理后回落线",
        description="集中整理把历史压回到预算的这一比例",
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
    active_fold: bool = Field(
        default=True,
        title="后台折叠",
        description="历史过了折叠线时，在后台请模型把已经用完的区间折成摘要，原文可取回；"
        "到预算线时先收起旧记录、不让角色停下来等整理。关掉则回到只靠集中整理",
    )
    fold_ratio: float = Field(
        default=0.4,
        gt=0.0,
        lt=1.0,
        title="折叠线",
        description="历史达到预算的这一比例时开始在后台请模型折叠，须低于提前整理线",
    )
    fold_keep_recent: int = Field(
        default=4,
        ge=1,
        le=20,
        title="折叠保留近期组数",
        description="最近这么多组调用与回执不允许折叠或收起",
        json_schema_extra={"x-ui-advanced": True},
    )
    fold_summary_max_chars: int = Field(
        default=3000,
        ge=200,
        le=12000,
        title="单段折叠摘要上限",
        description="模型为一段区间写的折叠摘要的最大字符数",
        json_schema_extra={"x-ui-advanced": True},
    )

    @model_validator(mode="after")
    def _check_fold_line(self) -> "MinecraftContextConfig":
        """折叠线必须低于提前整理线：模型主动折叠是第一道，集中整理只在它跟不上时才出手。"""
        if self.fold_ratio >= self.prepare_ratio:
            raise ValueError(
                f"fold_ratio={self.fold_ratio} 必须小于 prepare_ratio={self.prepare_ratio}："
                "折叠线不低于提前整理线时，集中整理会抢在模型折叠之前把历史压掉"
            )
        return self


class MinecraftPictureConfig(BaseConfig):
    """游戏 Agent 看画面（observe 的 picture 视图）的额度：一张图一两百 KB，看多了费钱也拖慢。

    超了额度不调 Mod，直接回一句改用结构化数据；Mod 自己只管同时一张、至少隔 1 秒。
    """

    max_per_task: int = Field(
        default=6,
        ge=0,
        title="每个任务最多看几次",
        description="同一个委派任务里最多看几次画面（0 = 不让看）；超了直接回一句改用结构化数据，不调 Mod",
    )
    min_interval_ms: int = Field(
        default=10_000,
        ge=0,
        title="两次至少隔多久",
        description="两次看画面之间至少隔多久（毫秒）；不够就先用结构化数据",
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
        picture: 看画面的额度（每个任务最多几次、两次至少隔多久）。
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
        default_factory=lambda: McpServerConfig(url=DEFAULT_MAICRAFT_MCP_URL),
        title="MCP 连接",
        description="连接游戏 Mod 的 MCP 服务地址（关闭后 Agent 降级运行）",
    )
    picture: MinecraftPictureConfig = Field(
        default_factory=MinecraftPictureConfig,
        title="看画面",
        description="游戏 Agent 看画面的次数与间隔（默认每个任务最多 6 次、两次至少隔 10 秒）",
    )


__all__ = ["MinecraftConfig", "MinecraftContextConfig", "MinecraftPictureConfig"]
