"""Input 阶段配置 Schema 定义

定义 ``config/input.toml`` 的 Pydantic 聚合模型。

包含：
- **每个 Collector 的 ConfigSchema 重导出**：从原 Collector 模块导入 ``ConfigSchema``，
  在此处用 ``XXXConfigSchema`` 别名导出，避免调用方硬编码 Collector 内部类名。
- **InputCollectorsConfig**：聚合所有 Collector 子配置的容器；
  每个 Collector 都是 ``Optional``，未启用则保持 ``None``。
- **InputPipelinesConfig**：与 ``core_schemas.pipelines`` 一致的动态键 dict 容器
  （为未来扩展预留，当前 ``input.toml`` 不含 pipeline 段，实际配置在
  ``core.toml`` 的 ``[pipelines.input.*]`` 中）。
- **InputConfig**：``config/input.toml`` 文件对应的根模型。
  字段：``type / collectors / pipelines``。

设计原则：
- 不修改 Collector 内部代码，仅导入并重导出其 ``ConfigSchema`` 嵌套类。
- 所有 Pydantic 模型继承 ``BaseConfig``，自动获得 ``from_dict_with_drift_check`` /
  ``generate_toml_string`` 等能力。
- ``InputConfig`` / ``InputCollectorsConfig`` 等聚合类设置 ``model_config = ConfigDict(extra="forbid")``
  拒绝未知的 collector 子段，避免拼写错误静默通过。
- **延迟导入**：Collector 模块通过 ``@collector`` 装饰器反向依赖本模块
  （``register_config_schema``），形成循环 import。解决方法：本模块
  顶层不 import Collector，仅在模型字段的 ``default_factory`` 中按需
  通过 ``_try_import_schema`` 延迟加载（同时缓存到模块级全局）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import ConfigDict, Field, model_validator

from .base import BaseConfig


# ---------------------------------------------------------------------------
# 延迟加载：Collector ConfigSchema 解析
# ---------------------------------------------------------------------------
#
# 背景：
#   1. ``base.py`` 在 import 时被 Collector 模块引用
#   2. Collector 模块通过 ``@collector`` 装饰器 → ``register_config_schema``
#      反向引用 ``src.modules.config.schemas`` 的导出
#   3. 若本模块在 import 阶段就 import Collector，会形成环
#   4. 解决：使用 ``_try_import_schema`` 在模型实例化时才真正 import
#
# 缓存：成功导入的 schema 类会被存到 ``_COLLECTOR_SCHEMA_CACHE`` 避免重复 import。

_COLLECTOR_SCHEMA_CACHE: Dict[str, Any] = {}  # value: type | _NEGATIVE_SENTINEL
_NEGATIVE_SENTINEL = "__failed__"  # 负缓存哨兵（区别于"未缓存"）


def _try_import_schema(collector_name: str) -> Optional[type]:
    """按需导入 Collector.ConfigSchema

    第一次调用时真正 import Collector 模块，之后从缓存读取。

    缓存策略：
    - **成功导入**：缓存为 schema 类，后续直接返回（避免重复 import 开销）
    - **失败导入**：缓存为 ``_NEGATIVE_SENTINEL``，但下次调用会重试

    为什么不用 ``None`` 缓存失败？因为 ``_try_import_schema`` 可能在循环 import 场景下
    被调用（Collector 模块加载中触发 → 导入失败 → 此时应允许重试），而 ``None``
    与"未配置"无法区分。

    Args:
        collector_name: Collector 类型标识（如 ``"console_input"`` / ``"stt"``）

    Returns:
        Collector.ConfigSchema 类，或 None（真实失败或非已知 collector）
    """
    if collector_name in _COLLECTOR_SCHEMA_CACHE:
        cached = _COLLECTOR_SCHEMA_CACHE[collector_name]
        if cached is not _NEGATIVE_SENTINEL:
            return cached
        # 负缓存：清空后重试（处理循环 import 场景：
        # Collector 模块加载中触发本函数 → 失败 → 后续调用应重试）
        del _COLLECTOR_SCHEMA_CACHE[collector_name]

    schema_cls: Optional[type] = None
    try:
        if collector_name == "bili_danmaku":
            from src.stages.input.collectors.bili_danmaku.bili_danmaku_collector import (  # noqa: E402
                BiliDanmakuCollector,
            )

            schema_cls = BiliDanmakuCollector.ConfigSchema
        elif collector_name == "bili_danmaku_official":
            from src.stages.input.collectors.bili_danmaku_official.bili_danmaku_official_collector import (  # noqa: E402
                BiliDanmakuOfficialCollector,
            )

            schema_cls = BiliDanmakuOfficialCollector.ConfigSchema
        elif collector_name == "console_input":
            from src.stages.input.collectors.console_input.console_input_collector import (  # noqa: E402
                ConsoleInputCollector,
            )

            schema_cls = ConsoleInputCollector.ConfigSchema
        elif collector_name == "mainosaba":
            from src.stages.input.collectors.mainosaba.mainosaba_collector import (  # noqa: E402
                MainosabaCollector,
            )

            schema_cls = MainosabaCollector.ConfigSchema
        elif collector_name == "mock_danmaku":
            from src.stages.input.collectors.mock_danmaku.mock_danmaku_collector import (  # noqa: E402
                MockDanmakuCollector,
            )

            schema_cls = MockDanmakuCollector.ConfigSchema
        elif collector_name == "read_pingmu":
            from src.stages.input.collectors.read_pingmu.read_pingmu_collector import (  # noqa: E402
                ReadPingmuCollector,
            )

            schema_cls = ReadPingmuCollector.ConfigSchema
        elif collector_name == "stt":
            from src.stages.input.collectors.stt.config import (  # noqa: E402
                STTInputConfig,
            )

            schema_cls = STTInputConfig
    except Exception:
        schema_cls = None

    # 缓存：None 失败用 sentinel 占位（下次会重试）
    _COLLECTOR_SCHEMA_CACHE[collector_name] = schema_cls if schema_cls is not None else _NEGATIVE_SENTINEL
    return schema_cls


# ---------------------------------------------------------------------------
# Collector 子配置（Optional，None 表示未启用）
# ---------------------------------------------------------------------------


def _optional_collector_field(collector_name: str) -> Any:
    """为 Pydantic Field 构造一个可空 Collector 子配置

    实际的 schema 类通过 ``_try_import_schema`` 延迟加载：
    - 若 Collector 已 import 则使用其 ConfigSchema
    - 若 Collector 未 import 或依赖缺失，则使用 ``Any`` 占位（仍允许 None 默认值）

    Args:
        collector_name: Collector 类型标识

    Returns:
        Pydantic FieldInfo（默认 None）
    """
    schema_cls = _try_import_schema(collector_name)
    if schema_cls is not None:
        desc = f"{schema_cls.__name__} Collector 配置"
    else:
        desc = f"{collector_name} Collector 配置（依赖未安装，配置不可用）"
    return Field(default=None, description=desc)


class InputCollectorsConfig(BaseConfig):
    """聚合所有 InputCollector 子配置

    每个 Collector 都是 ``Optional``：未启用或未填写时保持 ``None``，
    启用后填入完整子表 ``[collectors.<name>]`` 的内容。

    使用 ``extra="forbid"`` 拒绝未知 Collector 子段，避免拼写错误静默通过。

    实现说明：字段类型声明为 ``Optional[Any]`` 以避免循环 import
    （Collector 模块反向引用本模块）。通过 ``_validate_collectors`` model_validator
    在验证时将 dict 转换为对应的 ConfigSchema 实例，实现完整的字段约束。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: List[str] = Field(
        default_factory=list,
        description="启用的 Collector 名称列表",
    )

    bili_danmaku: Optional[Any] = _optional_collector_field("bili_danmaku")
    bili_danmaku_official: Optional[Any] = _optional_collector_field("bili_danmaku_official")
    console_input: Optional[Any] = _optional_collector_field("console_input")
    mainosaba: Optional[Any] = _optional_collector_field("mainosaba")
    mock_danmaku: Optional[Any] = _optional_collector_field("mock_danmaku")
    read_pingmu: Optional[Any] = _optional_collector_field("read_pingmu")
    stt: Optional[Any] = _optional_collector_field("stt")

    @model_validator(mode="after")
    def _validate_collectors(self) -> "InputCollectorsConfig":
        """将 dict 形式的 Collector 子段转换为对应的 ConfigSchema 实例

        字段类型声明为 ``Optional[Any]`` 是为了规避循环 import；
        此 validator 在 Pydantic 验证之后运行，将 dict 数据解析为真正的 schema 实例，
        这样后续字段访问（如 ``cfg.console_input.user_id``）能保持类型安全。
        """
        for collector_name in _PUBLIC_NAME_TO_COLLECTOR.values():
            value = getattr(self, collector_name, None)
            if value is None or not isinstance(value, dict):
                continue
            schema_cls = _try_import_schema(collector_name)
            if schema_cls is None:
                # 依赖未安装：保持原始 dict（调用方应处理）
                continue
            # 使用 from_dict 加载，自动剥离未知字段（配合 extra="forbid"）
            from_dict = getattr(schema_cls, "from_dict", None)
            if from_dict is not None:
                converted = from_dict(value)
            else:
                converted = schema_cls(**value)
            setattr(self, collector_name, converted)
        return self

    @classmethod
    def from_dict_with_drift_check(
        cls,
        data: Dict[str, Any],
    ):
        """Override 父类方法，支持 Any 类型 Collector 字段的递归漂移检测

        父类 ``BaseConfig.from_dict_with_drift_check`` 只对 ``BaseConfig`` 类型的字段
        做递归漂移检测。本类的 Collector 字段类型为 ``Optional[Any]``（为了规避循环
        import），所以需要在 override 中显式对每个 Collector 字段调用递归检测。
        """
        from .base import DriftReport

        report = DriftReport()

        class_fields = set(cls.model_fields.keys())
        data_keys = set(data.keys())

        # 顶层冗余字段
        for key in data_keys - class_fields:
            report.redundant.append(key)

        clean_data = {k: v for k, v in data.items() if k in class_fields}

        # 递归处理 Collector 字段（特殊处理 Any 类型）
        for collector_name in _PUBLIC_NAME_TO_COLLECTOR.values():
            if collector_name not in clean_data:
                continue
            nested_data = clean_data[collector_name]
            if not isinstance(nested_data, dict):
                continue
            schema_cls = _try_import_schema(collector_name)
            if schema_cls is None:
                # 依赖未安装：尝试用 from_dict 加载做最基本剥离
                from_dict = getattr(schema_cls, "from_dict", None) if schema_cls else None
                if from_dict is not None:
                    clean_data[collector_name] = from_dict(nested_data)
                continue
            # 递归漂移检测
            nested_instance, nested_report = schema_cls.from_dict_with_drift_check(nested_data)
            report.merge(collector_name, nested_report)
            clean_data[collector_name] = nested_instance

        instance = cls(**clean_data)
        # 重新触发 model_validator 完成 dict → schema 转换
        return instance, report


# ---------------------------------------------------------------------------
# Input 阶段 Pipeline 容器（与 core_schemas.pipelines 风格一致）
# ---------------------------------------------------------------------------


class InputPipelinesConfig(BaseConfig):
    """Input 阶段 Pipeline 配置容器

    使用动态键 dict 存储任意 pipeline（如 ``rate_limit`` / ``similar_filter`` 等）。

    注意：当前 ``config/input.toml`` 不含 pipeline 段，所有 Input Pipeline 实际配置
    在 ``config/core.toml`` 的 ``[pipelines.input.*]`` 中。此处保留容器类型
    以保持与 Output 阶段的对称性，并为未来扩展预留接口。
    """

    pipelines: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Input 阶段管道配置（动态键，如 rate_limit / similar_filter 等）",
    )


# ---------------------------------------------------------------------------
# 顶层 InputConfig（对应 config/input.toml）
# ---------------------------------------------------------------------------


class InputConfig(BaseConfig):
    """Input 阶段配置根类

    对应 ``config/input.toml`` 文件。

    TOML 结构示例::

        [collectors]
        enabled = ["console_input", "stt"]

        [collectors.console_input]
        type = "console_input"
        user_id = "console_user"
        user_nickname = "控制台"

        [collectors.stt]
        type = "stt"

        [collectors.stt.iflytek_asr]
        appid = "your_appid"
        api_key = "your_api_key"
        api_secret = "your_api_secret"
    """

    collectors: InputCollectorsConfig = Field(
        default_factory=InputCollectorsConfig,
        description="所有 Collector 子配置聚合（含 enabled 列表）",
    )

    pipelines: InputPipelinesConfig = Field(
        default_factory=InputPipelinesConfig,
        description="Input 阶段 Pipeline 配置",
    )


# ---------------------------------------------------------------------------
# 公共 API 别名：XXXConfigSchema
# ---------------------------------------------------------------------------
#
# 调用方通常期望使用 ``XXXConfigSchema`` 这样的稳定公开名（与 output_schemas.py 风格一致）。
# 此处使用 ``__getattr__`` 级别延迟加载：模块加载时不预解析任何 Collector，
# 第一次访问 ``BiliDanmakuConfigSchema`` 等别名时才触发 ``_try_import_schema``。
# 这是为了规避循环 import（Collector 模块在加载时会反向引用本模块）。

_PUBLIC_NAME_TO_COLLECTOR: Dict[str, str] = {
    "BiliDanmakuConfigSchema": "bili_danmaku",
    "BiliDanmakuOfficialConfigSchema": "bili_danmaku_official",
    "ConsoleInputConfigSchema": "console_input",
    "MainosabaConfigSchema": "mainosaba",
    "MockDanmakuConfigSchema": "mock_danmaku",
    "ReadPingmuConfigSchema": "read_pingmu",
    "STTConfigSchema": "stt",
}


def __getattr__(name: str) -> Any:
    """模块级延迟属性访问

    当访问 ``BiliDanmakuConfigSchema`` 等公开别名时触发，
    调用 ``_try_import_schema`` 解析真正的 ConfigSchema 类。
    这样保证模块加载阶段不会触发 Collector 模块的 import（避免循环依赖）。
    """
    if name in _PUBLIC_NAME_TO_COLLECTOR:
        return _try_import_schema(_PUBLIC_NAME_TO_COLLECTOR[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# 向后兼容导出
# ---------------------------------------------------------------------------


def get_input_collector_config(collector_type: str, config: Dict[str, Any]) -> Any:
    """通过类型名从注册表获取并验证 Collector 配置

    Args:
        collector_type: Collector 类型标识（如 ``"console_input"`` / ``"stt"``）
        config: 原始配置字典

    Returns:
        验证后的 Schema 实例（通常为 ``BaseConfig``）

    Raises:
        ValueError: Collector 类型未注册
        ValidationError: 配置验证失败
    """
    schema_class = _try_import_schema(collector_type)
    if schema_class is None:
        raise ValueError(
            f"未知的 InputCollector 类型: {collector_type!r}。支持: {sorted(_PUBLIC_NAME_TO_COLLECTOR.values())}"
        )
    # 通过 from_dict 加载，自动剥离未知字段（配合 extra="forbid"）
    from_dict = getattr(schema_class, "from_dict", None)
    if from_dict is not None:
        return from_dict(config)
    return schema_class(**config)


__all__ = [
    # Collector ConfigSchema 重导出（稳定公开名；通过 __getattr__ 延迟解析）
    "BiliDanmakuConfigSchema",  # noqa: F822
    "BiliDanmakuOfficialConfigSchema",  # noqa: F822
    "ConsoleInputConfigSchema",  # noqa: F822
    "MainosabaConfigSchema",  # noqa: F822
    "MockDanmakuConfigSchema",  # noqa: F822
    "ReadPingmuConfigSchema",  # noqa: F822
    "STTConfigSchema",  # noqa: F822
    # 聚合容器
    "InputCollectorsConfig",
    "InputPipelinesConfig",
    # 顶层根模型
    "InputConfig",
    # 工厂函数
    "get_input_collector_config",
    # 内部工具
    "_try_import_schema",
]


# 仅在类型检查时 import（运行时不需要，IDE/linter 友好）
if TYPE_CHECKING:
    from src.stages.input.collectors.bili_danmaku.bili_danmaku_collector import (  # noqa: F401
        BiliDanmakuCollector,
    )
    from src.stages.input.collectors.bili_danmaku_official.bili_danmaku_official_collector import (  # noqa: F401
        BiliDanmakuOfficialCollector,
    )
    from src.stages.input.collectors.console_input.console_input_collector import (  # noqa: F401
        ConsoleInputCollector,
    )
    from src.stages.input.collectors.mainosaba.mainosaba_collector import (  # noqa: F401
        MainosabaCollector,
    )
    from src.stages.input.collectors.mock_danmaku.mock_danmaku_collector import (  # noqa: F401
        MockDanmakuCollector,
    )
    from src.stages.input.collectors.read_pingmu.read_pingmu_collector import (  # noqa: F401
        ReadPingmuCollector,
    )
    from src.stages.input.collectors.stt.config import (  # noqa: F401
        STTInputConfig,
    )
