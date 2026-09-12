"""Tools 配置 Schema 定义

定义 ``config/tools.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    # 异步任务基建（执行委派原语：poll/wait 节拍）
    [tools.tasks]
    poll_interval_ms = 2000
    wait_timeout_ms = 1800000

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

    # 记忆分类（工具出口：query_memory；默认 enabled=true）
    [tools.memory]
    enabled = true

    # 通用 MCP 外部工具源通道
    [tools.mcp]
    enabled = true
    config.servers = {...}

    # 工具熔断器健康监控
    [tools.health]
    enabled = true
    failure_threshold = 3
    probe_interval_ms = 30000

设计原则：
- 工具提供者为「开关单元」：一个形象 / 一个 MCP server = 一个 enabled 开关。
  开 = 其全部工具进入可见集；关 = 全部消失。开关控制权归属人类（配置 + Web UI）。
- 感知已迁出至 ``collectors.toml``（``[collectors]`` 段）；输出已迁出至
  ``infra.toml``（``[tts]``/``[subtitle]``/``[dashboard.subtitle_widget]`` 等段）。
  本文件只承载工具域开关与异步任务基建。
- 组件字段由具体 Tool Provider 的 ConfigSchema 验证；本文件为聚合容器与元数据。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from src.modules.config.file_meta import FileMetaConfig
from src.modules.config.schemas.base import BaseConfig


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
    """记忆分类（工具出口 query_memory；LLM 主动检索关键词记忆）

    默认 enabled=true：缺省配置下 query_memory 已注册，与 main.py 消费端
    ``.get("enabled", True)`` 兜底对齐，消除原"配置 false 时仍注册"的漂移。
    """

    enabled: bool = Field(default=True, description="是否启用 query_memory 记忆检索工具")

    model_config = ConfigDict(extra="allow")


class McpProviderConfig(ToolProviderConfig):
    """通用 MCP 外部工具源分类（modules/mcp 通道；config.servers 声明 server 连接）。

    该通道只承载**通用** MCP——任何 Agent（主播 / 游戏）都可经 ToolRegistry
    看到并调用，定位是 Claude Code 风格的全局工具源。Agent 私有 MCP
    （仅服务于特定 Agent 的工具集，如 minecraft 专属）不走本段，
    而是写在 agents.toml 各 Agent 自己的段（位置即归属）：``[agents.<name>.mcp]``。
    装配即声明；调度与基建由全局 ToolRegistry + McpToolProvider 统一承担。
    """

    model_config = ConfigDict(extra="allow")


class ToolsTasksConfig(BaseConfig):
    """异步任务基建配置（``[tools.tasks]`` 段）

    提供执行委派原语的节拍配置：
    - ``poll_interval_ms``：跟踪循环轮询节拍（毫秒）
    - ``wait_timeout_ms``：等待任务完成的最长时长（毫秒）

    段定义与运行期消费分离：本任务负责段定义；端到端消费断言随工具线任务合流
    由其覆盖（本任务交付即定义完成，消费侧硬错残留由工具线在合流日清理）。
    """

    poll_interval_ms: int = Field(
        default=2000,
        ge=100,
        description="异步任务跟踪循环轮询间隔（毫秒）",
    )
    wait_timeout_ms: int = Field(
        default=1_800_000,
        ge=1000,
        description="异步任务最长等待时长（毫秒）",
    )


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

    包含工具提供者开关（avatar/studio/vision/memory/mcp）+ 异步任务基建 +
    工具熔断器配置 + disabled_tools 平铺列表。使用 ``extra="forbid"`` 拒绝未知子段。
    """

    model_config = ConfigDict(extra="forbid")

    # 异步任务基建节拍（段定义归本文件；消费侧由工具线任务合流覆盖）
    tasks: ToolsTasksConfig = Field(
        default_factory=ToolsTasksConfig,
        description="异步任务基建节拍（poll_interval_ms / wait_timeout_ms）",
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
        description="记忆分类（工具出口 query_memory；默认 enabled=true）",
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

    meta: FileMetaConfig = Field(default_factory=FileMetaConfig, description="文件元数据")
    tools: ToolsConfig = Field(
        default_factory=ToolsConfig,
        description="[tools] 段聚合（异步任务基建 + 各提供者分类配置 + disabled_tools）",
    )


__all__ = [
    # 工具提供者开关基类
    "ToolProviderConfig",
    # 分类配置
    "AvatarProviderConfig",
    "StudioProviderConfig",
    "VisionProviderConfig",
    "MemoryProviderConfig",
    "McpProviderConfig",
    # 异步任务基建
    "ToolsTasksConfig",
    # 工具熔断器健康监控
    "ToolsHealthConfig",
    # 聚合
    "ToolsConfig",
    # 顶层根模型
    "ToolsRootConfig",
]
