"""Agent 配置 Schema 定义

定义 ``config/agents.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [agents]
    enabled = ["streamer", "minecraft", "text_adv"]

    [agents.streamer]  # 主播 Agent（包内权威：src/agents/streamer/config.py）
    [agents.minecraft]  # Minecraft 游戏 Agent（包内权威：src/agents/minecraft/config.py）
    [agents.text_adv]  # 文字冒险游戏 Agent（包内权威：src/agents/text_adv/config.py）

设计原则：
- 业务 Agent 统一经 ``[agents]`` 段注册启用
- Agent 间无分类层——每个 Agent 是一份顶级子配置，**自己拥有全部字段**
  （无 ``[agents.game]`` 公共段，无 ``engine`` 判别字段）
- 组件配置权威在自身包内（``src/agents/<name>/config.py``），中央树只聚合引用
- ``enabled`` 列表接受已知 Agent 名（streamer / minecraft / text_adv），
  未知名由 Pydantic 校验拒绝（extra="forbid" + Literal 约束）
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import ConfigDict, Field

from src.modules.config.file_meta import FileMetaConfig
from src.modules.config.schemas.base import BaseConfig

# 包内权威 Schema 的延迟 import 与 model_rebuild 在文件末尾执行
# （避免顶部 import 触发过重的依赖链）


# ---------------------------------------------------------------------------
# Agent 类型字面量
# ---------------------------------------------------------------------------


# 顶级 Agent 注册名（与 SUPPORTED_AGENTS 同步；变更时一起改）。
AgentType = Literal[
    "streamer",  # 主播 Agent（Planner+Replyer）
    "minecraft",  # Minecraft 游戏 Agent
    "text_adv",  # 文字冒险游戏 Agent
]


# ---------------------------------------------------------------------------
# [agents] 段聚合
# ---------------------------------------------------------------------------


class AgentsConfig(BaseConfig):
    """[agents] 段聚合

    包含所有业务 Agent 的启用列表与子配置。

    使用 ``extra="forbid"`` 拒绝未知 Agent 子段，避免拼写错误静默通过。

    各 Agent 子配置字段类型由对应包内权威 Schema 提供（双轨消灭：中央树
    不再内联字段定义，避免漂移写回路径把同一字段在两处定义）；
    ``default_factory`` 构造默认实例，确保加载器把空字段补齐为完整子树。
    """

    model_config = ConfigDict(extra="forbid")

    # 启用列表（哪些 Agent 参与运行）——取值必须为已知顶级 Agent 名
    enabled: List[AgentType] = Field(
        default_factory=lambda: ["streamer"],
        description="启用的 Agent 列表",
        json_schema_extra={
            "x-options": ["streamer", "minecraft", "text_adv"],
        },
    )

    # 各 Agent 的子配置（包内权威）
    # 类型用字符串前向引用（避免顶部循环 import）；model_rebuild() 在模块末尾解析
    # 为真实类。default_factory 用 __import__ 延迟加载，确保字段类型在
    # AgentsConfig.model_rebuild() 之前可用且不触发重型依赖链。
    streamer: Optional["StreamerConfig"] = Field(  # type: ignore[name-defined]  # noqa: F821
        default_factory=lambda: __import__("src.agents.streamer.config", fromlist=["StreamerConfig"]).StreamerConfig(),
        description="主播 Agent（Planner+Replyer）配置",
        json_schema_extra={"x-ui-type": "object"},
    )
    minecraft: Optional["MinecraftConfig"] = Field(  # type: ignore[name-defined]  # noqa: F821
        default_factory=lambda: __import__(
            "src.agents.minecraft.config", fromlist=["MinecraftConfig"]
        ).MinecraftConfig(),
        description="Minecraft 游戏 Agent 配置",
        json_schema_extra={"x-ui-type": "object"},
    )
    text_adv: Optional["TextAdvConfig"] = Field(  # type: ignore[name-defined]  # noqa: F821
        default_factory=lambda: __import__("src.agents.text_adv.config", fromlist=["TextAdvConfig"]).TextAdvConfig(),
        description="文字冒险游戏 Agent 配置",
        json_schema_extra={"x-ui-type": "object"},
    )


# ---------------------------------------------------------------------------
# 顶层根模型（对应 config/agents.toml）
# ---------------------------------------------------------------------------


class AgentsRootConfig(BaseConfig):
    """Agents 配置根类

    对应 ``config/agents.toml`` 文件。``[agents.streamer]`` 子段完整承载原
    ``persona`` / ``context`` / ``background`` 过渡段的字段；中央树不再持有
    顶层 persona/context/background 镜像。
    """

    __file_name__ = "agents.toml"
    __section_label__ = "🤖 业务 Agent"

    meta: FileMetaConfig = Field(default_factory=FileMetaConfig, description="文件元数据")
    agents: AgentsConfig = Field(
        default_factory=AgentsConfig,
        description="[agents] 段聚合（启用列表 + 各 Agent 子配置）",
    )


# 延迟 import：各 Agent 包内权威 Schema；通过 model_rebuild() 完成前向引用解析。
from src.agents.minecraft.config import MinecraftConfig  # noqa: E402, F401
from src.agents.streamer.config import StreamerConfig  # noqa: E402, F401
from src.agents.text_adv.config import TextAdvConfig  # noqa: E402, F401

# 重新解析 forward refs
AgentsConfig.model_rebuild()


__all__ = [
    # Agent 类型
    "AgentType",
    # 聚合
    "AgentsConfig",
    # 顶层根模型
    "AgentsRootConfig",
]
