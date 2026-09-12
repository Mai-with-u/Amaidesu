"""单文件配置元数据模型

每个配置文件自带一个 ``[meta]`` 段，承载该文件自己的结构版本——
版本流按文件独立递增，互不联动。
"""

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig

# 新布局六文件的起始版本基线；此后各文件独立递增
CONFIG_BASELINE_VERSION = "2.0.31"


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
