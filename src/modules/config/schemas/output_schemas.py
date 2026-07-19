"""Output 阶段配置 Schema 定义

定义 `config/output.toml` 的 Pydantic 聚合模型。

文件结构（TOML 视角）::

    [handlers]
    enabled = ["subtitle", "vts"]
    concurrent_rendering = true
    error_handling = "continue"  # continue | stop
    render_timeout_ms = 10000  # 0 = 无限制
    completion_timeout_ms = 30000  # 两层事件聚合超时(毫秒),0 表示不限制

    [handlers.subtitle]
    type = "subtitle"
    window_width = 800
    ...

    [pipelines]  # 可选，future use
    [pipelines.profanity_filter]
    enabled = true
    words = ["测试脏话"]

加载后对应字典::

    {
        "handlers": {
            "enabled": [...],
            "concurrent_rendering": True,
            "error_handling": "continue",
            "render_timeout_ms": 10000,
            "completion_timeout_ms": 30000,
            "subtitle": {...},
            ...
        },
        "pipelines": {
            "profanity_filter": {...},
            ...
        }
    }

包含：

- **每个 Handler 的 ConfigSchema 重导出**：从原 Handler 模块导入 `ConfigSchema`，
  在此处用 `XXXConfigSchema` 别名导出，避免调用方硬编码 Handler 内部类名。
- **OutputHandlersConfig**：`[handlers]` 段的聚合模型。
  包含启用元数据（enabled / concurrent_rendering / error_handling / render_timeout_ms /
  completion_timeout_ms）以及每个 Handler 的可选子配置。
- **OutputPipelinesConfig**：`[pipelines]` 段（默认空 dict）。
- **OutputConfig**：`config/output.toml` 文件对应的根模型。

设计原则：
- 不修改 Handler 内部代码，仅在调用时延迟加载其 `ConfigSchema` 嵌套类。
- 所有 Pydantic 模型继承 `BaseConfig`，自动获得 `from_dict_with_drift_check` /
  `generate_toml_string` 等能力。
- 顶层枚举/布尔字段携带 `json_schema_extra` UI 元数据，供 Dashboard 前端动态表单使用。
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field

from .base import BaseConfig


# ---------------------------------------------------------------------------
# 每个 Handler 的 ConfigSchema 延迟加载
# ---------------------------------------------------------------------------
#
# 这些类由 `@handler("name")` 装饰器在 Handler 模块加载时自动注册到
# `CONFIG_SCHEMA_REGISTRY`（见 `src.modules.config.schemas.__init__`）。
# 此处仅在调用方实际访问 `*ConfigSchema` 符号时才 import 对应 Handler 模块，
# 一来避免 handler 模块被本配置模块反向 import（可能触发循环依赖），
# 二来与旧 `_try_import_obs_control_schema` 的延迟加载风格保持一致。


def _try_load_handler_schema(module_path: str, class_name: str) -> Optional[type]:
    """延迟加载某个 Handler 模块并返回其 `ConfigSchema`。

    Args:
        module_path: Handler 模块路径（如 `src.stages.output.handlers....`）
        class_name: Handler 类名

    Returns:
        `ConfigSchema` 类型，或 None（导入失败/依赖缺失时）
    """
    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name, None)
        if cls is None:
            return None
        return getattr(cls, "ConfigSchema", None)
    except Exception:
        return None


# 公共别名：XXXConfigSchema -> Handler.ConfigSchema
# 调用方可通过这些符号访问对应 Handler 的 ConfigSchema，
# 无需关心 Handler 模块的 import 时机。

DebugConsoleConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.debug_console.debug_console_handler",
    "DebugConsoleHandler",
)
EdgeTTSConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.audio.edge_tts.edge_tts_handler",
    "EdgeTTSHandler",
)
GPTSoVITSConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.audio.gptsovits.gptsovits_handler",
    "GPTSoVITSHandler",
)
ObsControlConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.obs_control.obs_control_handler",
    "ObsControlHandler",
)
OmniTTSConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.audio.omni_tts.omni_tts_handler",
    "OmniTTSHandler",
)
RemoteStreamConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.remote_stream.remote_stream_handler",
    "RemoteStreamHandler",
)
StickerConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.sticker.sticker_handler",
    "StickerHandler",
)
SubtitleConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.subtitle.subtitle_handler",
    "SubtitleHandler",
)
VRChatConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.avatar.vrchat.vrchat_handler",
    "VRChatHandler",
)
VTSConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.avatar.vts.vts_handler",
    "VTSHandler",
)
WarudoConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.stages.output.handlers.avatar.warudo.warudo_handler",
    "WarudoHandler",
)


# ---------------------------------------------------------------------------
# Handler 子配置（Optional，None 表示未启用）
# ---------------------------------------------------------------------------


def _optional_handler_field(schema_cls: Optional[type]) -> Any:
    """为 Pydantic Field 构造一个可空 Handler 子配置；依赖缺失时退化为 None 字段。

    Args:
        schema_cls: Handler ConfigSchema 类，或 None（依赖未安装时）

    Returns:
        Pydantic FieldInfo（默认 None）
    """
    if schema_cls is None:
        # 依赖缺失：保留字段，但默认 None 并允许 None
        return Field(default=None, description="依赖未安装，配置不可用")
    return Field(
        default=None,
        description=f"{schema_cls.__name__} Handler 配置",
    )


class OutputHandlersConfig(BaseConfig):
    """`[handlers]` 段聚合模型

    包含 Output 阶段运行元数据（enabled / concurrent_rendering / error_handling /
    render_timeout_ms / completion_timeout_ms）以及每个 Handler 的可选子配置。

    使用 `extra="forbid"` 拒绝未知 Handler 子段，避免拼写错误静默通过。
    """

    model_config = ConfigDict(extra="forbid")

    # ----- 元数据 -----
    enabled: List[str] = Field(
        default_factory=list,
        description="启用的 Handler 名称列表",
    )
    concurrent_rendering: bool = Field(
        default=True,
        description="是否并发渲染（true: 多个 Handler 并行；false: 顺序执行）",
        json_schema_extra={"x-ui-type": "boolean"},
    )
    error_handling: Literal["continue", "stop"] = Field(
        default="continue",
        description="错误处理策略：continue 继续执行其他 Handler；stop 出错立即停止",
        json_schema_extra={
            "x-ui-type": "select",
            "x-options": ["continue", "stop"],
        },
    )
    render_timeout_ms: int = Field(
        default=10000,
        ge=0,
        description="单个 Handler 渲染超时（毫秒），0 表示不限制",
        json_schema_extra={"x-ui-type": "integer", "x-min": 0},
    )
    completion_timeout_ms: int = Field(
        default=30000,
        ge=0,
        description="两层事件聚合 watchdog 超时(毫秒):per-handler 完成事件超时未到齐则强制"
        "发 OUTPUT_INTENT_FINISHED 并 warn 日志。0 表示不启用 watchdog。",
        json_schema_extra={"x-ui-type": "integer", "x-min": 0},
    )

    # ----- 每个 Handler 的可选子配置 -----
    debug_console: Optional[Any] = _optional_handler_field(DebugConsoleConfigSchema)
    edge_tts: Optional[Any] = _optional_handler_field(EdgeTTSConfigSchema)
    gptsovits: Optional[Any] = _optional_handler_field(GPTSoVITSConfigSchema)
    obs_control: Optional[Any] = _optional_handler_field(ObsControlConfigSchema)
    omni_tts: Optional[Any] = _optional_handler_field(OmniTTSConfigSchema)
    remote_stream: Optional[Any] = _optional_handler_field(RemoteStreamConfigSchema)
    sticker: Optional[Any] = _optional_handler_field(StickerConfigSchema)
    subtitle: Optional[Any] = _optional_handler_field(SubtitleConfigSchema)
    vrchat: Optional[Any] = _optional_handler_field(VRChatConfigSchema)
    vts: Optional[Any] = _optional_handler_field(VTSConfigSchema)
    warudo: Optional[Any] = _optional_handler_field(WarudoConfigSchema)


# ---------------------------------------------------------------------------
# Output 阶段 Pipeline 容器（与 core_schemas.pipelines 风格一致）
# ---------------------------------------------------------------------------


class OutputPipelinesConfig(BaseConfig):
    """`[pipelines]` 段容器

    使用动态键 dict 存储任意 pipeline（如 `profanity_filter` 等）。
    键名不限，由具体 pipeline 自行解析；值是 free-form 字典。

    当前 `config/output.toml` 不强制写入此段，但保留以便未来扩展；
    默认空 dict，缺省时不影响 `OutputConfig` 验证。
    """

    pipelines: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Output 阶段管道配置（动态键，如 profanity_filter 等）",
    )


# ---------------------------------------------------------------------------
# 顶层 OutputConfig（对应 config/output.toml 根）
# ---------------------------------------------------------------------------


class OutputConfig(BaseConfig):
    """Output 阶段配置根类

    对应 `config/output.toml` 文件。

    加载后的字典形态::

        {
            "handlers": {
                "enabled": [...],
                "concurrent_rendering": True,
                ...,
                "<handler_name>": {...}
            },
            "pipelines": {
                "<pipeline_name>": {...}
            }
        }
    """

    handlers: OutputHandlersConfig = Field(
        default_factory=OutputHandlersConfig,
        description="`[handlers]` 段聚合",
    )

    pipelines: OutputPipelinesConfig = Field(
        default_factory=OutputPipelinesConfig,
        description="`[pipelines]` 段聚合（可选，默认空）",
    )


# ---------------------------------------------------------------------------
# 向后兼容导出
# ---------------------------------------------------------------------------
#
# 保留旧 API 表面以避免破坏既有调用方。
# 注：实际 Schema 注册由 `@handler()` 装饰器在导入 Handler 模块时自动完成；
# OUTPUT_CONFIG_MAP 保留为空 dict 仅作为占位。

OUTPUT_CONFIG_MAP: Dict[str, type] = {}


def get_output_config(handler_type: str, config: Dict[str, Any]) -> Any:
    """通过类型名从注册表获取并验证 Handler 配置

    Args:
        handler_type: Handler 类型标识（如 `"subtitle"` / `"vts"`）
        config: 原始配置字典

    Returns:
        验证后的 Schema 实例（通常为 `BaseConfig`）

    Raises:
        KeyError: Handler 类型未注册
        ValidationError: 配置验证失败
    """
    from src.modules.config.schemas import get_config_schema

    schema_class = get_config_schema(handler_type, phase="output")
    from_dict = getattr(schema_class, "from_dict", None)
    if from_dict is not None:
        return from_dict(config)
    return schema_class(**config)


__all__ = [
    # Handler ConfigSchema 重导出（稳定公开名，可能为 None 表示依赖缺失）
    "DebugConsoleConfigSchema",
    "EdgeTTSConfigSchema",
    "GPTSoVITSConfigSchema",
    "ObsControlConfigSchema",
    "OmniTTSConfigSchema",
    "RemoteStreamConfigSchema",
    "StickerConfigSchema",
    "SubtitleConfigSchema",
    "VRChatConfigSchema",
    "VTSConfigSchema",
    "WarudoConfigSchema",
    # 聚合容器
    "OutputHandlersConfig",
    "OutputPipelinesConfig",
    # 顶层根模型
    "OutputConfig",
    # 向后兼容
    "OUTPUT_CONFIG_MAP",
    "get_output_config",
]  # noqa: F401
