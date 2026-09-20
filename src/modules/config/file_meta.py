"""单文件配置元数据模型

每个配置文件自带一个 ``[meta]`` 段，承载该文件自己的结构版本——各文件的
版本流彻底独立：一个文件的版本只随作用于它的升级钩子推进，无钩子则保持
原值，文件之间互不联动。
"""

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig

# 新生成配置文件的初始版本种子（仅作为 FileMetaConfig.version 的 default
# 落盘；既有文件的推进由升级钩子调度决定，此常量不参与调度）
CONFIG_BASELINE_VERSION = "2.0.36"


class FileMetaConfig(BaseConfig):
    """配置文件元数据（每个配置文件的 ``[meta]`` 段）

    Attributes:
        version: 该文件的结构版本。只读——由升级流程自动推进，
            不接受用户手写或 WebUI 修改。
    """

    version: str = Field(
        default=CONFIG_BASELINE_VERSION,
        description="配置结构版本（只读，由升级流程自动推进）",
        json_schema_extra={"readonly": True},
    )


__all__ = ["CONFIG_BASELINE_VERSION", "FileMetaConfig"]
