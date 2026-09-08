"""Tools 配置 Schema 定义

定义 ``config/tools.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [tools]
    enabled = ["perception", "output"]

    # 虚拟形象分类（每形象一 provider 实例；enabled 控制其工具可见性）
    [tools.avatar.vts]
    enabled = true
    config = {...}

    [tools.avatar.warudo]
    enabled = false

    # 演播室分类
    [tools.studio.obs]
    enabled = true
    config = {...}

    # 视觉基础模块（工具出口：look_at_screen）
    [tools.vision]
    enabled = true
    config = {...}

    # 记忆分类（工具出口：query_memory）
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
    "perception",  # 采集器包：屏幕/音频/弹幕/遥测（采集器通道，不迁移）
    "output",  # 输出包：TTS/字幕/皮套/OBS（保留包级列表，实际开关在提供者级）
]


# ---------------------------------------------------------------------------
# 工具提供者开关（单一事实源：每个提供者一个开关单元）
# ---------------------------------------------------------------------------


class ToolProviderConfig(BaseConfig):
    """工具提供者开关基类（提供者级 enabled + 自由 config）

    Attributes:
        enabled: 是否启用该提供者（开 = 其工具全部可见）
        config: 提供者具体配置（动态键，由对应 Tool Provider 注入 Schema 验证）
    """

    enabled: bool = Field(default=True, description="是否启用该工具提供者（开=其工具全部可见）")
    config: Dict[str, Any] = Field(
        default_factory=dict,
        description="提供者具体配置（由对应 Tool Provider 注入 Schema 后验证）",
    )


class AvatarProviderConfig(ToolProviderConfig):
    """虚拟形象分类的提供者开关（每个形象一实例：[tools.avatar].<name>.enabled）

    ``[tools.avatar.vts]`` / ``[tools.avatar.warudo]`` 等动态段：
    每个形象 = 一个开关单元，开 = 其全部工具进入可见集。
    """

    model_config = ConfigDict(extra="allow")


class StudioProviderConfig(ToolProviderConfig):
    """演播室分类的提供者开关（``[tools.studio.obs]`` 等动态段）"""

    model_config = ConfigDict(extra="allow")


class VisionProviderConfig(ToolProviderConfig):
    """视觉基础模块（工具出口 look_at_screen；被调才看，快照型）"""

    model_config = ConfigDict(extra="allow")


class MemoryProviderConfig(ToolProviderConfig):
    """记忆分类（工具出口 query_memory；LLM 主动检索关键词记忆）"""

    model_config = ConfigDict(extra="allow")


class McpProviderConfig(ToolProviderConfig):
    """MCP 外部工具源分类（modules/mcp 通道；config.servers 声明 server 连接）"""

    model_config = ConfigDict(extra="allow")


class ToolsHealthConfig(BaseConfig):
    """工具熔断器健康监控配置（``[tools.health]`` 段）

    控制 ToolRegistry 的连续失败熔断行为与 ToolHealthMonitor 探活节拍：
    - ``enabled``：是否装配 ToolHealthMonitor（false 时只保留 registry 侧熔断判定，monitor 不启动）
    - ``failure_threshold``：连续失败次数达阈值即熔断摘除（``<= 0`` 关闭熔断）
    - ``probe_interval_ms``：monitor 对熔断工具的探活节拍毫秒；同样用作
      "熔断后最小驻留时长"——monitor 在 dwell 时间未到时不会尝试恢复
    """

    enabled: bool = Field(default=True, description="是否启用 ToolHealthMonitor 探活循环")
    failure_threshold: int = Field(
        default=3,
        description="ToolRegistry 连续失败熔断阈值（<=0 关闭熔断，monitor 仍可装配但永不跳闸）",
    )
    probe_interval_ms: int = Field(
        default=30000,
        gt=0,
        description="ToolHealthMonitor 探活节拍毫秒；兼作熔断后最小驻留时长",
    )


# ---------------------------------------------------------------------------
# [tools] 段聚合
# ---------------------------------------------------------------------------


class ToolsConfig(BaseConfig):
    """[tools] 段聚合

    包含所有能力包元数据 + 工具提供者开关（avatar/studio/vision/memory/mcp）。
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
    perception: Optional[ToolProviderConfig] = Field(
        default=None,
        description="感知工具包（采集器配置：屏幕/音频/弹幕/遥测）",
        json_schema_extra={"x-ui-type": "object"},
    )
    output: Optional[ToolProviderConfig] = Field(
        default=None,
        description="输出工具包（TTS/字幕配置保留段；皮套/OBS 开关下放到提供者级）",
        json_schema_extra={"x-ui-type": "object"},
    )

    # 工具提供者开关（单一事实源；动态子段：avatar.<name> / studio.<name>）
    avatar: Optional[Dict[str, AvatarProviderConfig]] = Field(
        default_factory=dict,
        description="虚拟形象分类（每形象一实例：vts / warudo / vrchat ...，enabled 控制各形象工具）",
        json_schema_extra={"x-ui-type": "object"},
    )
    studio: Optional[Dict[str, StudioProviderConfig]] = Field(
        default_factory=dict,
        description="演播室分类（obs 等，enabled 控制各演播工具）",
        json_schema_extra={"x-ui-type": "object"},
    )
    vision: Optional[VisionProviderConfig] = Field(
        default=None,
        description="视觉基础模块（工具出口 look_at_screen；enabled=true 时组合根注入 Pillow 后端）",
        json_schema_extra={"x-ui-type": "object"},
    )
    memory: Optional[MemoryProviderConfig] = Field(
        default=None,
        description="记忆分类（工具出口 query_memory；enabled=true 时注册记忆检索工具）",
        json_schema_extra={"x-ui-type": "object"},
    )
    mcp: Optional[McpProviderConfig] = Field(
        default=None,
        description="通用 MCP 外部工具源（config.servers 声明连接；enabled=true 时注册其工具）",
        json_schema_extra={"x-ui-type": "object"},
    )

    # 工具熔断器健康监控（ToolRegistry 熔断 + ToolHealthMonitor 探活）
    health: ToolsHealthConfig = Field(
        default_factory=ToolsHealthConfig,
        description="工具熔断器健康监控（连续失败熔断 + 探活恢复）",
        json_schema_extra={"x-ui-type": "object"},
    )

    # 停用的工具名列表：工具仍全量注册（工具页可见全集），但默认对 LLM 不可见
    # 且调用被拒绝；由组合根在装配完成后应用到 ToolRegistry
    disabled_tools: List[str] = Field(
        default_factory=list,
        description="停用的工具名列表（对 LLM 不可见且不可调用，重启后生效）",
        json_schema_extra={"x-ui-type": "multiselect"},
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
        description="[tools] 段聚合（启用列表 + 各能力包/提供者分类配置）",
    )


__all__ = [
    # 工具能力包类型
    "ToolPackType",
    # 工具提供者开关基类
    "ToolProviderConfig",
    # 分类配置
    "AvatarProviderConfig",
    "StudioProviderConfig",
    "VisionProviderConfig",
    "MemoryProviderConfig",
    "McpProviderConfig",
    # 工具熔断器健康监控
    "ToolsHealthConfig",
    # 聚合
    "ToolsConfig",
    # 顶层根模型
    "ToolsRootConfig",
]
