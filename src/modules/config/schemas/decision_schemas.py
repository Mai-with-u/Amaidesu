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

try:
    from src.stages.decision.deciders.amaidesu.amaidesu_decider import (  # type: ignore[no-redef]  # v2.0.0 兼容：保留顶层 import 以不破坏外部 import AmaidesuDecider 的代码
        AmaidesuDecider,
    )
    from src.stages.decision.deciders.command.command_decider import (  # type: ignore[no-redef]
        CommandDecider,
    )
    from src.stages.decision.deciders.llm.llm_decider import (  # type: ignore[no-redef]
        LLMDecider,
    )
    from src.stages.decision.deciders.maibot.maibot_decider import (  # type: ignore[no-redef]
        MaiBotDecider,
    )
    from src.stages.decision.deciders.replay.replay_decider import (  # type: ignore[no-redef]
        ReplayDecider,
    )

    _DECIDER_CLASSES_AVAILABLE = True
except ImportError:
    _DECIDER_CLASSES_AVAILABLE = False
    AmaidesuDecider = None  # type: ignore[misc,assignment]
    CommandDecider = None  # type: ignore[misc,assignment]
    LLMDecider = None  # type: ignore[misc,assignment]
    MaiBotDecider = None  # type: ignore[misc,assignment]
    ReplayDecider = None  # type: ignore[misc,assignment]


# 所有 Decider 共享的 type 标识字面量
DeciderType = Literal["llm", "maibot", "amaidesu", "command", "replay"]


def _try_load_decider_classes() -> None:
    """尝试延迟加载 stages/decision/deciders/ 的 Decider 类，失败不抛异常。"""
    global _AmaidesuDecider_cls, _MaiBotDecider_cls, _LLMDecider_cls, _CommandDecider_cls, _ReplayDecider_cls
    if _AmaidesuDecider_cls is not None:
        return
    try:
        from src.stages.decision.deciders.amaidesu.amaidesu_decider import AmaidesuDecider
        from src.stages.decision.deciders.maibot.maibot_decider import MaiBotDecider
        from src.stages.decision.deciders.llm.llm_decider import LLMDecider
        from src.stages.decision.deciders.command.command_decider import CommandDecider
        from src.stages.decision.deciders.replay.replay_decider import ReplayDecider

        _AmaidesuDecider_cls = AmaidesuDecider
        _MaiBotDecider_cls = MaiBotDecider
        _LLMDecider_cls = LLMDecider
        _CommandDecider_cls = CommandDecider
        _ReplayDecider_cls = ReplayDecider
    except Exception as exc:  # noqa: BLE001 - 兼容期容忍缺失
        import logging

        logging.getLogger(__name__).debug(f"Decider 类延迟导入失败（兼容期正常）: {exc}")


_AmaidesuDecider_cls = None
_MaiBotDecider_cls = None
_LLMDecider_cls = None
_CommandDecider_cls = None
_ReplayDecider_cls = None


def _get_AmaidesuDecider():
    if _AmaidesuDecider_cls is None:
        _try_load_decider_classes()
    return _AmaidesuDecider_cls


def _get_MaiBotDecider():
    if _MaiBotDecider_cls is None:
        _try_load_decider_classes()
    return _MaiBotDecider_cls


def _get_LLMDecider():
    if _LLMDecider_cls is None:
        _try_load_decider_classes()
    return _LLMDecider_cls


def _get_CommandDecider():
    if _CommandDecider_cls is None:
        _try_load_decider_classes()
    return _CommandDecider_cls


def _get_ReplayDecider():
    if _ReplayDecider_cls is None:
        _try_load_decider_classes()
    return _ReplayDecider_cls


class LLMDeciderConfigSchema(BaseConfig):
    """LLM Decider 配置 Schema（含 Dashboard UI 元数据，v2.0.0 兼容期壳）

    v2.0.0 已由 ``agents_schemas.py`` 的 ``StreamerAgentConfig`` 替代。
    保留本类仅为不破坏外部旧代码引用；实际字段定义由 stages/ 模块维护。
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


class MaiBotDeciderConfigSchema(BaseConfig):
    """MaiBot Decider 配置 Schema（v2.0.0 兼容期壳）"""

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


class AmaidesuDeciderConfigSchema(BaseConfig):
    """Amaidesu Decider 配置 Schema（v2.0.0 兼容期壳）

    v2.0.0 已由 ``agents_schemas.StreamerAgentConfig`` 替代。
    """

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


class CommandDeciderConfigSchema(BaseConfig):
    """Command Decider 配置 Schema（v2.0.0 兼容期壳）"""

    command_prefix: str = Field(
        default="/",
        description="命令前缀",
        json_schema_extra={"x-ui-type": "string"},
    )


class ReplayDeciderConfigSchema(BaseConfig):
    """Replay Decider 配置 Schema（v2.0.0 兼容期壳）

    当前只有 1 个字段（add_default_action），无需 UI 元数据扩展，
    但保留子类壳以便统一命名约定与未来扩展。
    """

    add_default_action: bool = Field(
        default=True,
        description="是否在输入流末尾补一个默认动作",
    )


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
