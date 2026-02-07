"""
日志配置 Schema

定义日志系统的配置结构。
"""

from typing import Literal
from pydantic import BaseModel, Field


class LoggingConfig(BaseModel):
    """日志配置"""

    enabled: bool = Field(
        default=True,
        description="启用文件日志"
    )

    format: Literal["jsonl", "text"] = Field(
        default="jsonl",
        description="日志格式：jsonl（每行一个JSON对象）或 text（纯文本）"
    )

    directory: str = Field(
        default="logs",
        description="日志目录（相对于项目根目录）"
    )

    level: str = Field(
        default="INFO",
        description="最低日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）"
    )

    rotation: str = Field(
        default="10 MB",
        description="日志轮转触发条件（如 '10 MB', '500 MB', '1 GB'）"
    )

    retention: str = Field(
        default="7 days",
        description="日志保留时间（如 '7 days', '1 week', '1 month'）"
    )

    compression: str = Field(
        default="zip",
        description="压缩格式（zip, gz, tar, tar.gz）"
    )

    split_by_session: bool = Field(
        default=False,
        description="是否按会话分割日志文件（每次启动生成新文件，文件名包含时间戳）"
    )

    @classmethod
    def generate_toml(cls) -> str:
        """生成 TOML 配置模板

        Returns:
            TOML 格式的配置字符串
        """
        return """# 日志配置
[logging]
# 启用文件日志
enabled = true
# 日志格式：jsonl（每行一个JSON对象）或 text（纯文本）
format = "jsonl"
# 日志目录（相对于项目根目录）
directory = "logs"
# 最低日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
level = "INFO"
# 日志轮转触发条件（如 '10 MB', '500 MB', '1 GB'）
rotation = "10 MB"
# 日志保留时间（如 '7 days', '1 week', '1 month'）
retention = "7 days"
# 压缩格式（zip, gz, tar, tar.gz）
compression = "zip"
# 是否按会话分割日志文件（默认：false）
# 启用后，每次启动生成新文件（文件名包含时分秒，如 amaidesu_20260207_150423.jsonl）
# 禁用时，同一天多次启动会追加到同一文件（如 amaidesu_2026-02-07.jsonl）
split_by_session = false
"""

    class Config:
        """Pydantic 配置"""
        json_schema_extra = {
            "example": {
                "enabled": True,
                "format": "jsonl",
                "directory": "logs",
                "level": "INFO",
                "rotation": "10 MB",
                "retention": "7 days",
                "compression": "zip",
                "split_by_session": False
            }
        }
