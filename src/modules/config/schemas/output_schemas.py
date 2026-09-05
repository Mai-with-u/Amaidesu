"""Output 阶段配置 Schema 定义

定义 `config/output.toml` 的 Pydantic 聚合模型。

文件结构（TOML 视角）::

    [handlers]
    enabled = ["subtitle", "vts"]

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
  仅含 ``enabled`` 元数据与每个 Handler 的可选子配置。
- **OutputConfig**：`config/output.toml` 文件对应的根模型。

  > 原 `OutputPipelinesConfig` 容器已删除——OutputPipeline 定案移除，
  > 敏感词净化归 Replyer（ProfanityFilter）。

v2.0.10 收尾清理（调度字段下放至 core [tts]）：
- 移除 ``concurrent_rendering``：v1 输出阶段并发模型已废弃，无消费者
- 移除 ``error_handling``：v1 错误策略字段，无消费者
- 移除 ``completion_timeout_ms``：v1 两层事件聚合 watchdog，无消费者
- 移除 ``render_timeout_ms``：v1 单 Handler 渲染超时；调度语义升级为 TTS
  基础设施总超时，统一上移至 core.toml ``[tts].render_timeout_ms``，
  由 v2.0.10 的 ``CrossFileMigration`` 完成迁移

设计原则：
- 不修改 Handler 内部代码，仅在调用时延迟加载其 `ConfigSchema` 嵌套类。
- 所有 Pydantic 模型继承 `BaseConfig`，自动获得 `from_dict_with_drift_check` /
  `generate_toml_string` 等能力。
- 顶层枚举/布尔字段携带 `json_schema_extra` UI 元数据，供 Dashboard 前端动态表单使用。
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

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


# 公共别名：XXXConfigSchema -> Provider.ConfigSchema
# 调用方可通过这些符号访问对应 Provider 的 ConfigSchema，
# 无需关心 Provider 模块的 import 时机。
#
# 所有 handler 已改为 modules/tools/output/ 下的 Provider。
# DebugConsoleHandler 已被 dump_intent() 函数替代，无 ConfigSchema。

DebugConsoleConfigSchema: Optional[type] = None  # dump_intent 替代（DebugConfig dataclass）
EdgeTTSConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.modules.tools.output.tts.edge_tts_tool",
    "EdgeTTSProvider",
)
GPTSoVITSConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.modules.tools.output.tts.gptsovits_tool",
    "GPTSoVITSProvider",
)
ObsControlConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.modules.tools.output.obs.obs_provider",
    "OBSProvider",
)
OmniTTSConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.modules.tools.output.tts.omni_tts_tool",
    "OmniTTSProvider",
)
VoiceBoxConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.modules.tools.output.tts.voicebox_tool",
    "VoiceboxProvider",
)
RemoteStreamConfigSchema: Optional[type] = None  # 协议层无 ConfigSchema（保留消息类型）
SubtitleConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.modules.tools.output.subtitle.subtitle_service",
    "SubtitleGuiService",
)
VRChatConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.modules.tools.output.vts.vrchat_provider",
    "VRChatProvider",
)
VTSConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.modules.tools.output.vts.vts_provider",
    "VTSProvider",
)
WarudoConfigSchema: Optional[type] = _try_load_handler_schema(
    "src.modules.tools.output.warudo.warudo_provider",
    "WarudoProvider",
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

    包含 Output 阶段运行元数据（``enabled``）以及每个 Handler 的可选子配置。

    v2.0.10 起不再持有调度字段——并发渲染 / 错误策略 / 渲染超时 / 聚合 watchdog
    均已废弃，统一迁移至 core.toml ``[tts]`` 的 TTS 基础设施配置。

    使用 `extra="forbid"` 拒绝未知 Handler 子段，避免拼写错误静默通过。
    """

    model_config = ConfigDict(extra="forbid")

    # ----- 元数据 -----
    enabled: List[str] = Field(
        default_factory=list,
        description="启用的 Handler 名称列表",
    )

    # ----- 每个 Handler 的可选子配置 -----
    debug_console: Optional[Any] = _optional_handler_field(DebugConsoleConfigSchema)
    edge_tts: Optional[Any] = _optional_handler_field(EdgeTTSConfigSchema)
    gptsovits: Optional[Any] = _optional_handler_field(GPTSoVITSConfigSchema)
    obs_control: Optional[Any] = _optional_handler_field(ObsControlConfigSchema)
    omni_tts: Optional[Any] = _optional_handler_field(OmniTTSConfigSchema)
    voicebox: Optional[Any] = _optional_handler_field(VoiceBoxConfigSchema)
    remote_stream: Optional[Any] = _optional_handler_field(RemoteStreamConfigSchema)
    subtitle: Optional[Any] = _optional_handler_field(SubtitleConfigSchema)
    vrchat: Optional[Any] = _optional_handler_field(VRChatConfigSchema)
    vts: Optional[Any] = _optional_handler_field(VTSConfigSchema)
    warudo: Optional[Any] = _optional_handler_field(WarudoConfigSchema)


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
            }
        }
    """

    handlers: OutputHandlersConfig = Field(
        default_factory=OutputHandlersConfig,
        description="`[handlers]` 段聚合",
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
    "VoiceBoxConfigSchema",
    "RemoteStreamConfigSchema",
    "SubtitleConfigSchema",
    "VRChatConfigSchema",
    "VTSConfigSchema",
    "WarudoConfigSchema",
    # 聚合容器
    "OutputHandlersConfig",
    # 顶层根模型
    "OutputConfig",
    # 向后兼容
    "OUTPUT_CONFIG_MAP",
    "get_output_config",
]  # noqa: F401
