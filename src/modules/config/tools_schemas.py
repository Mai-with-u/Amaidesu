"""Tools 配置 Schema 定义（v2.0.0）

定义 ``config/tools.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [tools]
    enabled = ["perception", "understanding", "output", "external"]

    [tools.perception.danmaku]
    platform = "bilibili"
    room_id = 12345
    ...

    [tools.output.tts]
    voice = "zh-CN-XiaoxiaoNeural"
    ...

设计原则：
- 替代旧版 [collectors]（感知）和 [handlers]（输出）阶段组件注册
- 感知/理解/输出按"能力包"分组（而非按阶段）
- 工具配置细节由具体 Tool Provider 提供（Pydantic ConfigSchema 在 W3 落地）
- 本文件提供聚合容器与元数据，组件字段留待 ToolRegistry 注入（W3 接入）
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field

from src.modules.config.schemas.base import BaseConfig


# ---------------------------------------------------------------------------
# 工具能力包类型
# ---------------------------------------------------------------------------


ToolPackType = Literal[
    "perception",  # 感知：屏幕/音频/弹幕/遥测
    "understanding",  # 理解：VLM/ASR
    "output",  # 输出：TTS/字幕/皮套/OBS
    "content_engine",  # 内容引擎：游戏控制器
    "external",  # 外部：MCP（外部工具源）
]


# ---------------------------------------------------------------------------
# 通用工具包元数据
# ---------------------------------------------------------------------------


class ToolPackMeta(BaseConfig):
    """工具包通用元数据（每个能力包共享）

    Attributes:
        enabled: 是否启用此工具包
        provider: 提供方（builtin/game/mcp）
        config: 提供方具体配置（动态键，W3 由 ToolRegistry 注入具体 Schema）
    """

    enabled: bool = Field(default=True, description="是否启用此工具包")
    provider: Literal["builtin", "game", "mcp"] = Field(
        default="builtin",
        description="工具包提供方（builtin=框架内置, game=游戏 Agent, mcp=MCP 外部源）",
        json_schema_extra={
            "x-ui-type": "select",
            "x-options": ["builtin", "game", "mcp"],
        },
    )
    config: Dict[str, Any] = Field(
        default_factory=dict,
        description="工具包具体配置（由对应 Tool Provider 注入 Schema 后验证）",
    )


# ---------------------------------------------------------------------------
# 兼容旧组件的容器（保留名字以利迁移期 Dashboard 显示）
# ---------------------------------------------------------------------------
# 旧版 [collectors.bili_danmaku] / [handlers.subtitle] 等组件级配置，
# 在 tools.toml 中归入对应工具包的 config 字典里。
# 例如：
#   [tools.perception.config.bili_danmaku]
#   platform = "bilibili"
#   room_id = 12345
#   [tools.output.config.subtitle]
#   window_width = 800
#
# 不为每个组件定义独立 BaseConfig（避免组件字段耦合到 Schema），
# W3 由 ToolRegistry 在启动时对 config dict 做 Pydantic 校验。


# ---------------------------------------------------------------------------
# [tools] 段聚合
# ---------------------------------------------------------------------------


class LookAtScreenToolConfig(BaseConfig):
    """[tools.look_at_screen] 屏幕快照同步工具配置。

    ``look_at_screen`` 是 L2 DI 工具（快照型→被调才看）：组合根在
    ``enabled=true`` 时注入 Pillow 截图后端并注册到 ToolRegistry。
    与 ``[tools.perception]``（采集器包配置，流型事件源）**无关**——
    命名分属两个域，勿混。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="是否在组合根注册 look_at_screen 工具（游戏 Agent 感知依赖它）",
    )
    default_max_width: int = Field(
        default=1280,
        ge=0,
        description="默认图像缩放最大宽度（像素，0=不缩放；省 token 用）",
    )


class ToolsConfig(BaseConfig):
    """[tools] 段聚合

    包含所有工具包（perception/understanding/output/content_engine/external）的元数据。

    使用 ``extra="forbid"`` 拒绝未知工具包子段，避免拼写错误静默通过。
    """

    model_config = ConfigDict(extra="forbid")

    # 启用的工具包列表
    enabled: List[ToolPackType] = Field(
        default_factory=lambda: ["perception", "output"],
        description="启用的工具包列表",
        json_schema_extra={
            "x-ui-type": "multiselect",
            "x-options": [
                "perception",
                "understanding",
                "output",
                "content_engine",
                "external",
            ],
        },
    )

    # 各工具包子配置（均为 Optional，未启用时 None）
    perception: Optional[ToolPackMeta] = Field(
        default=None,
        description="感知工具包（屏幕/音频/弹幕/遥测）",
        json_schema_extra={"x-ui-type": "object"},
    )
    understanding: Optional[ToolPackMeta] = Field(
        default=None,
        description="理解工具包（VLM/ASR）",
        json_schema_extra={"x-ui-type": "object"},
    )
    output: Optional[ToolPackMeta] = Field(
        default=None,
        description="输出工具包（TTS/字幕/皮套/OBS）",
        json_schema_extra={"x-ui-type": "object"},
    )
    content_engine: Optional[ToolPackMeta] = Field(
        default=None,
        description="内容引擎（游戏控制器）",
        json_schema_extra={"x-ui-type": "object"},
    )
    external: Optional[ToolPackMeta] = Field(
        default=None,
        description="外部工具源（MCP Provider，配置在 core.toml [mcp] 段）",
        json_schema_extra={"x-ui-type": "object"},
    )
    look_at_screen: Optional[LookAtScreenToolConfig] = Field(
        default_factory=LookAtScreenToolConfig,
        description="屏幕快照同步工具（L2 DI，被调才看；enabled=true 时组合根注入 Pillow 后端）",
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
        description="[tools] 段聚合（启用列表 + 各工具包子配置）",
    )


__all__ = [
    # 工具能力包类型
    "ToolPackType",
    # 工具包元数据
    "ToolPackMeta",
    # 聚合
    "ToolsConfig",
    # 顶层根模型
    "ToolsRootConfig",
]
