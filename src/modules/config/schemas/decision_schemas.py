"""Decision 阶段配置 Schema 定义

定义 `config/decision.toml` 的 Pydantic 聚合模型。

文件结构（TOML 视角）::

    [deciders]
    enabled = ["amaidesu"]

    [deciders.llm]
    type = "llm"
    client = "llm"
    ...

    [deciders.maibot]
    type = "maibot"
    host = "localhost"
    ...

加载后对应字典::

    {
        "deciders": {
            "enabled": ["amaidesu"],
            "llm": {"type": "llm", "client": "llm", ...},
            "maibot": {"type": "maibot", "host": "localhost", ...},
            "amaidesu": {"type": "amaidesu", ...},
            "replay": {"type": "replay", ...},
            "command": {"type": "command", ...},
        }
    }

包含：

- **每个 Decider 的 ConfigSchema 重导出**：从原 Decider 模块导入 `ConfigSchema`，
  在此处扩展（添加 UI 元数据 / 类型别名），形成稳定的公开名 `<Decider>ConfigSchema`，
  避免调用方直接依赖 Decider 内部嵌套类名。
- **DecisionDecidersConfig**：`[deciders]` 段的聚合模型，
  包含启用元数据（enabled）以及每个 Decider 的可选子配置。
- **DecisionPipelinesConfig**：`[pipelines]` 段（默认空 dict，保留扩展位）。
- **DecisionConfig**：`config/decision.toml` 文件对应的根模型。

设计原则：
- 不修改 Decider 内部代码，仅导入并扩展其 `ConfigSchema` 嵌套类。
- 所有 Pydantic 模型继承 `BaseConfig`，自动获得 `from_dict_with_drift_check` /
  `generate_toml_string` 等能力。
- 顶层字段携带 `json_schema_extra` UI 元数据（x-ui-type / x-options / x-min / x-max），
  供 Dashboard 前端动态表单使用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field

from .base import BaseConfig

# ---------------------------------------------------------------------------
# 每个 Decider 的 ConfigSchema 重导出（含 UI 元数据扩展）
# ---------------------------------------------------------------------------
#
# 这些类由 `@decider("name")` 装饰器在 Decider 模块加载时自动注册到
# `CONFIG_SCHEMA_REGISTRY`。
# 此处的 `<Decider>ConfigSchema` 是继承 + 字段重定义版本，专门携带
# Dashboard UI 元数据，**不修改原 Decider 代码**。
#
# 字段重定义规则：
# - 必须保持与父类完全相同的 annotation / default / description
# - 仅追加 json_schema_extra
# - Pydantic v2 允许子类重定义字段元数据

from src.stages.decision.deciders.amaidesu.amaidesu_decider import (  # noqa: F401
    AmaidesuDecider,
)
from src.stages.decision.deciders.command.command_decider import (  # noqa: F401
    CommandDecider,
)
from src.stages.decision.deciders.llm.llm_decider import (  # noqa: F401
    LLMDecider,
)
from src.stages.decision.deciders.maibot.maibot_decider import (  # noqa: F401
    MaiBotDecider,
)
from src.stages.decision.deciders.replay.replay_decider import (  # noqa: F401
    ReplayDecider,
)


# 所有 Decider 共享的 type 标识字面量
DeciderType = Literal["llm", "maibot", "amaidesu", "command", "replay"]


class LLMDeciderConfigSchema(LLMDecider.ConfigSchema):
    """LLM Decider 配置 Schema（含 Dashboard UI 元数据）

    继承自 `LLMDecider.ConfigSchema`，本类仅追加 UI 元数据。
    真实字段定义仍由 Decider 模块维护，避免重复定义。
    """

    client: Literal["llm", "llm_fast", "vlm"] = Field(
        default="llm",
        description="使用的LLM客户端名称",
        json_schema_extra={
            "x-ui-type": "select",
            "x-options": ["llm", "llm_fast", "vlm"],
        },
    )
    fallback_mode: Literal["simple", "echo", "error"] = Field(
        default="simple",
        description="降级模式",
        json_schema_extra={
            "x-ui-type": "select",
            "x-options": ["simple", "echo", "error"],
        },
    )


class MaiBotDeciderConfigSchema(MaiBotDecider.ConfigSchema):
    """MaiBot Decider 配置 Schema（含 Dashboard UI 元数据）"""

    host: str = Field(
        default="localhost",
        description="MaiBot WebSocket服务器主机地址",
        json_schema_extra={"x-ui-type": "string"},
    )
    port: int = Field(
        default=8000,
        description="MaiBot WebSocket服务器端口",
        ge=1,
        le=65535,
        json_schema_extra={"x-ui-type": "integer", "x-min": 1, "x-max": 65535},
    )
    platform: str = Field(
        default="amaidesu",
        description="平台标识符",
        json_schema_extra={"x-ui-type": "string"},
    )
    connect_timeout: float = Field(
        default=10.0,
        description="连接超时时间（秒）",
        gt=0,
        json_schema_extra={"x-ui-type": "number", "x-min": 0, "x-step": 0.5},
    )
    reconnect_interval: float = Field(
        default=5.0,
        description="重连间隔时间（秒）",
        gt=0,
        json_schema_extra={"x-ui-type": "number", "x-min": 0, "x-step": 0.5},
    )


class AmaidesuDeciderConfigSchema(AmaidesuDecider.ConfigSchema):
    """Amaidesu Decider 配置 Schema（含 Dashboard UI 元数据）"""

    client: Literal["llm", "llm_fast", "vlm"] = Field(
        default="llm_fast",
        description="内容决策使用的 LLM 客户端",
        json_schema_extra={
            "x-ui-type": "select",
            "x-options": ["llm", "llm_fast", "vlm"],
        },
    )
    fallback_mode: Literal["silent", "simple", "echo"] = Field(
        default="silent",
        description="LLM 失败时的降级模式：silent 不发言 / simple 通用回复 / echo 复述",
        json_schema_extra={
            "x-ui-type": "select",
            "x-options": ["silent", "simple", "echo"],
        },
    )


class CommandDeciderConfigSchema(CommandDecider.ConfigSchema):
    """Command Decider 配置 Schema（含 Dashboard UI 元数据）"""

    command_prefix: str = Field(
        default="/",
        description="命令前缀",
        json_schema_extra={"x-ui-type": "string"},
    )


class ReplayDeciderConfigSchema(ReplayDecider.ConfigSchema):
    """Replay Decider 配置 Schema（含 Dashboard UI 元数据）

    当前只有 1 个字段（add_default_action），无需 UI 元数据扩展，
    但保留子类壳以便统一命名约定与未来扩展。
    """


# ---------------------------------------------------------------------------
# Decider 子配置字段构造助手
# ---------------------------------------------------------------------------


def _optional_decider_field(schema_cls: type) -> Any:
    """为 Pydantic Field 构造一个可空 Decider 子配置。"""
    return Field(
        default=None,
        description=f"{schema_cls.__name__} Decider 配置",
        json_schema_extra={"x-ui-type": "object"},
    )


# ---------------------------------------------------------------------------
# [deciders] 段聚合模型
# ---------------------------------------------------------------------------


class DecisionDecidersConfig(BaseConfig):
    """`[deciders]` 段聚合模型

    包含 Decision 阶段运行元数据（enabled）以及每个 Decider 的可选子配置。

    使用 `extra="forbid"` 拒绝未知 Decider 子段，避免拼写错误静默通过。
    """

    model_config = ConfigDict(extra="forbid")

    # ----- 启用列表 -----
    enabled: List[DeciderType] = Field(
        default_factory=list,
        description="启用的 Decider 列表（可多选，所有启用的 Decider 将并行处理消息）",
        json_schema_extra={
            "x-ui-type": "multiselect",
            "x-options": ["llm", "maibot", "amaidesu", "command", "replay"],
        },
    )

    # ----- 每个 Decider 的可选子配置 -----
    llm: Optional[LLMDeciderConfigSchema] = _optional_decider_field(LLMDeciderConfigSchema)
    maibot: Optional[MaiBotDeciderConfigSchema] = _optional_decider_field(MaiBotDeciderConfigSchema)
    amaidesu: Optional[AmaidesuDeciderConfigSchema] = _optional_decider_field(AmaidesuDeciderConfigSchema)
    command: Optional[CommandDeciderConfigSchema] = _optional_decider_field(CommandDeciderConfigSchema)
    replay: Optional[ReplayDeciderConfigSchema] = _optional_decider_field(ReplayDeciderConfigSchema)


# ---------------------------------------------------------------------------
# Decision 阶段 Pipeline 容器（预留扩展）
# ---------------------------------------------------------------------------


class DecisionPipelinesConfig(BaseConfig):
    """`[pipelines]` 段容器

    使用动态键 dict 存储任意 pipeline。键名不限，由具体 pipeline 自行解析；
    值是 free-form 字典。

    当前 `config/decision.toml` 不强制写入此段，但保留以便未来扩展；
    默认空 dict，缺省时不影响 `DecisionConfig` 验证。
    """

    pipelines: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Decision 阶段管道配置（动态键，预留扩展位）",
    )


# ---------------------------------------------------------------------------
# 顶层 DecisionConfig（对应 config/decision.toml 根）
# ---------------------------------------------------------------------------


class DecisionConfig(BaseConfig):
    """Decision 阶段配置根类

    对应 `config/decision.toml` 文件。

    加载后的字典形态::

        {
            "deciders": {
                "enabled": [...],
                "llm": {...},
                "maibot": {...},
                "amaidesu": {...},
                "command": {...},
                "replay": {...},
            },
            "pipelines": {"<pipeline_name>": {...}},
        }
    """

    deciders: DecisionDecidersConfig = Field(
        default_factory=DecisionDecidersConfig,
        description="`[deciders]` 段聚合",
    )

    pipelines: DecisionPipelinesConfig = Field(
        default_factory=DecisionPipelinesConfig,
        description="`[pipelines]` 段聚合（可选，默认空）",
    )


# ---------------------------------------------------------------------------
# 向后兼容导出
# ---------------------------------------------------------------------------

__all__ = [
    # Decider ConfigSchema 重导出（稳定公开名）
    "LLMDeciderConfigSchema",
    "MaiBotDeciderConfigSchema",
    "AmaidesuDeciderConfigSchema",
    "CommandDeciderConfigSchema",
    "ReplayDeciderConfigSchema",
    # 聚合容器
    "DecisionDecidersConfig",
    "DecisionPipelinesConfig",
    # 顶层根模型
    "DecisionConfig",
    # 类型别名
    "DeciderType",
]
