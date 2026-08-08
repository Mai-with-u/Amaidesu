"""模拟服务注册系统 - @simulator 装饰器

为模拟器注册 ConfigSchema 和 UI 元数据到全局注册表。

与 @collector 装饰器模式对齐，但不注册到 _COLLECTORS（模拟器不是采集器）。
注册的 key 固定为 "simulator"（全局唯一模拟器）。
"""

from typing import TypeVar

T = TypeVar("T")


def simulator(cls: type[T]) -> type[T]:
    """将类注册为模拟器

    自动注册类属性 ``ConfigSchema`` 到 ``CONFIG_SCHEMA_REGISTRY``，
    以及 UI 元数据（label/description）到 ``COMPONENT_UI_REGISTRY``。
    注册 key 固定为 ``"simulator"``。

    要求被装饰类定义 ``ConfigSchema`` 类属性（通常是独立的 ``BaseConfig`` 子类）。
    """

    from src.modules.config.schemas import (
        register_component_ui,
        register_config_schema,
    )

    config_schema = getattr(cls, "ConfigSchema", None)
    if config_schema is not None:
        register_config_schema("simulator", config_schema)

    register_component_ui("simulator", "模拟直播间", "LLM 驱动的模拟直播间调试工具")

    return cls
