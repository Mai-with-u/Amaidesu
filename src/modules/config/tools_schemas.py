"""Tools 配置 Schema 定义

定义 ``config/tools.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [tools]
    enabled = ["perception", "output"]

    # 形象域（每形象一 provider 实例；enabled 控制其工具可见性）
    [tools.avatar.vts]
    enabled = true
    config = {...}

    [tools.avatar.warudo]
    enabled = false

    # 演播域
    [tools.studio.obs]
    enabled = true
    config = {...}

    # 视觉基础模块（工具出口：look_at_screen）
    [tools.vision]
    enabled = true
    config = {...}

    # 记忆域（工具出口：query_memory）
    [tools.memory]
    enabled = true

    # 通用 MCP 外部工具源通道
    [tools.mcp]
    enabled = true
    config.servers = {...}

设计原则：
- 工具提供者为「开关单元」：一个形象 / 一个 MCP server = 一个 enabled 开关。
  开 = 其全部工具进入可见集；关 = 全部消失。开关控制权归属人类（配置 + Web UI）。
- 感知/理解/输出按"能力包"分组（而非按阶段）；本文件为聚合容器与元数据，
  组件字段由具体 Tool Provider 的 ConfigSchema 验证。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field

from src.modules.config.schemas.base import BaseConfig


# ---------------------------------------------------------------------------
# 工具能力包类型
# ---------------------------------------------------------------------------


ToolPackType = Literal[
    "perception",  # 采集器包：屏幕/音频/弹幕/遥测（采集器域，不迁移）
    "output",  # 输出包：TTS/字幕/皮套/OBS（保留包级列表，实际开关在域级）
]


# ---------------------------------------------------------------------------
# 工具域开关（单一事实源：每个提供者一个开关单元）
# ---------------------------------------------------------------------------


class ToolDomainConfig(BaseConfig):
    """工具域开关基类（域级 enabled + 提供方自由 config）

    Attributes:
        enabled: 是否启用该域（开 = 域下工具全部可见）
        config: 提供方具体配置（动态键，由对应 Tool Provider 注入 Schema 验证）
    """

    enabled: bool = Field(default=True, description="是否启用该工具域（开=其工具全部可见）")
    config: Dict[str, Any] = Field(
        default_factory=dict,
        description="工具域具体配置（由对应 Tool Provider 注入 Schema 后验证）",
    )


class AvatarDomainConfig(ToolDomainConfig):
    """形象域开关（每个形象一实例：files[avatar].<name>.enabled）

    ``[tools.avatar.vts]`` / ``[tools.avatar.warudo]`` 等动态段：
    每个形象 = 一个开关单元，开 = 其全部工具进入可见集。
    """

    model_config = ConfigDict(extra="allow")


class StudioDomainConfig(ToolDomainConfig):
    """演播域开关（``[tools.studio.obs]`` 等动态段）"""

    model_config = ConfigDict(extra="allow")


class VisionDomainConfig(ToolDomainConfig):
    """视觉基础模块（工具出口 look_at_screen；被调才看，快照型）"""

    model_config = ConfigDict(extra="allow")


class MemoryDomainConfig(ToolDomainConfig):
    """记忆域（工具出口 query_memory；LLM 主动检索关键词记忆）"""

    model_config = ConfigDict(extra="allow")


class McpDomainConfig(ToolDomainConfig):
    """通用 MCP 外部工具源域（modules/mcp 通道；config.servers 声明 server 连接）"""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# [tools] 段聚合
# ---------------------------------------------------------------------------


class ToolsConfig(BaseConfig):
    """[tools] 段聚合

    包含所有能力包元数据 + 工具域开关（avatar/studio/vision/memory/mcp）。
    使用 ``extra="forbid"`` 拒绝未知子段，避免拼写错误静默通过。
    """

    model_config = ConfigDict(extra="forbid")

    # 启用的工具包列表（保留兼容：perception/output）
    enabled: List[ToolPackType] = Field(
        default_factory=lambda: ["perception", "output"],
        description="启用的工具包列表",
        json_schema_extra={
            "x-ui-type": "multiselect",
            "x-options": ["perception", "output"],
        },
    )

    # 各能力包子配置（均为 Optional，未启用时 None）
    perception: Optional[ToolDomainConfig] = Field(
        default=None,
        description="感知工具包（采集器配置：屏幕/音频/弹幕/遥测）",
        json_schema_extra={"x-ui-type": "object"},
    )
    output: Optional[ToolDomainConfig] = Field(
        default=None,
        description="输出工具包（TTS/字幕配置保留段；皮套/OBS 开关下放到域级）",
        json_schema_extra={"x-ui-type": "object"},
    )

    # 工具域开关（单一事实源；动态子段：avatar.<name> / studio.<name>）
    avatar: Optional[Dict[str, AvatarDomainConfig]] = Field(
        default_factory=dict,
        description="形象域（每形象一实例：vts / warudo / vrchat ...，enabled 控制各形象工具）",
        json_schema_extra={"x-ui-type": "object"},
    )
    studio: Optional[Dict[str, StudioDomainConfig]] = Field(
        default_factory=dict,
        description="演播域（obs 等，enabled 控制各演播工具）",
        json_schema_extra={"x-ui-type": "object"},
    )
    vision: Optional[VisionDomainConfig] = Field(
        default=None,
        description="视觉基础模块（工具出口 look_at_screen；enabled=true 时组合根注入 Pillow 后端）",
        json_schema_extra={"x-ui-type": "object"},
    )
    memory: Optional[MemoryDomainConfig] = Field(
        default=None,
        description="记忆域（工具出口 query_memory；enabled=true 时注册记忆检索工具）",
        json_schema_extra={"x-ui-type": "object"},
    )
    mcp: Optional[McpDomainConfig] = Field(
        default=None,
        description="通用 MCP 外部工具源（config.servers 声明连接；enabled=true 时注册其工具）",
        json_schema_extra={"x-ui-type": "object"},
    )


# ---------------------------------------------------------------------------
# 顶层根模型（对应 config/tools.toml）
# ---------------------------------------------------------------------------


class ToolsRootConfig(BaseConfig):
    """Tools 配置根类

    对应 ``config/tools.toml`` 文件。
    """

    tools: ToolsConfig = Field(
        default_factory=ToolsConfig,
        description="[tools] 段聚合（启用列表 + 各能力包/工具域配置）",
    )


__all__ = [
    # 工具能力包类型
    "ToolPackType",
    # 工具域开关基类
    "ToolDomainConfig",
    # 域配置
    "AvatarDomainConfig",
    "StudioDomainConfig",
    "VisionDomainConfig",
    "MemoryDomainConfig",
    "McpDomainConfig",
    # 聚合
    "ToolsConfig",
    # 顶层根模型
    "ToolsRootConfig",
]
