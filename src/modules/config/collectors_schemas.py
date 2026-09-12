"""采集器配置根模型（config/collectors.toml）

框架按 ``[collectors].enabled`` 名单装配采集器；各采集器的
配置段（``[collectors.<name>]``）由其包内 ConfigSchema 定义，
经组件注册表动态装配进本根模型的动态段。
"""

from pydantic import ConfigDict, Field

from src.modules.config.file_meta import FileMetaConfig
from src.modules.config.schemas.base import BaseConfig


class CollectorsRootConfig(BaseConfig):
    """采集器配置根（对应 ``config/collectors.toml`` 文件）

    顶层字段：
    - ``meta``：文件元数据（版本号 + 描述，独立于配置本体）
    - ``enabled``：启用的采集器列表（名单驱动装配）

    各采集器的具体配置段（``[collectors.console_input]`` 等）由
    ``extra="allow"`` 透传——因为 5 个采集器的 ConfigSchema 类型各异，
    无法在静态类定义里聚合。每个 ``[collectors.<name>]`` 子段的具体形状
    由对应采集器包内 ConfigSchema 验证，multi_file_loader 在加载时按
    注册表分发校验与漂移检测。
    """

    model_config = ConfigDict(extra="allow")

    __file_name__ = "collectors.toml"
    __section_label__ = "📥 采集器"

    meta: FileMetaConfig = Field(default_factory=FileMetaConfig, description="文件元数据")
    enabled: list[str] = Field(
        default_factory=lambda: ["console_input"],
        description="启用的 Collector 列表（注册名 = 配置段名）",
        json_schema_extra={
            "x-options": [
                "bili_danmaku",
                "bili_danmaku_official",
                "console_input",
                "screen",
                "stt",
            ],
        },
    )


__all__ = ["CollectorsRootConfig"]
