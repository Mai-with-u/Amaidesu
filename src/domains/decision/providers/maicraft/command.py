"""
命令数据结构模块

定义解析后的命令数据结构。
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.modules.types.base.normalized_message import NormalizedMessage


@dataclass
class Command:
    """命令数据结构，包含解析后的命令信息"""

    name: str  # 命令名称，如 "chat"
    args: list[str]  # 参数列表，如 ["hello", "world"]
    raw_args: str  # 原始参数字符串，如 "hello world"
    original_message: "NormalizedMessage"  # 原始标准化消息对象

    def get_arg(self, index: int, default: str = "") -> str:
        """
        获取指定位置的参数。

        Args:
            index: 参数索引（从0开始）
            default: 默认值

        Returns:
            参数值或默认值
        """
        return self.args[index] if 0 <= index < len(self.args) else default

    def get_args_from(self, start_index: int) -> list[str]:
        """
        获取从指定索引开始的所有参数。

        Args:
            start_index: 起始索引

        Returns:
            参数列表
        """
        return [] if start_index >= len(self.args) else self.args[start_index:]

    def join_args(self, start_index: int = 0, separator: str = " ") -> str:
        """
        将参数连接成字符串。

        Args:
            start_index: 起始索引
            separator: 分隔符

        Returns:
            连接后的字符串
        """
        args_to_join = self.get_args_from(start_index)
        return separator.join(args_to_join)

    @property
    def has_args(self) -> bool:
        """检查是否有参数"""
        return len(self.args) > 0

    @property
    def arg_count(self) -> int:
        """获取参数数量"""
        return len(self.args)
