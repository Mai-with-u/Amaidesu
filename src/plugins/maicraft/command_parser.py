import re
import shlex
from typing import Optional
from maim_message import MessageBase
from .command_data import Command
from src.utils.logger import get_logger


class CommandParser:
    """命令解析器，负责解析命令格式并创建Command对象"""

    def __init__(self, command_prefix: str = "/"):
        self.command_prefix = command_prefix
        self.logger = get_logger("CommandParser")

        # 编译命令匹配正则表达式
        escaped_prefix = re.escape(command_prefix)
        self.command_pattern = re.compile(rf"^{escaped_prefix}(\w+)(?:\s+(.*))?$")

    def parse_command(self, text: str, original_message: MessageBase) -> Optional[Command]:
        """
        解析命令文本。

        Args:
            text: 要解析的文本
            original_message: 原始消息对象

        Returns:
            解析后的Command对象，如果不是命令则返回None
        """
        if not text or not text.strip():
            return None

        text = text.strip()
        match = self.command_pattern.match(text)

        if not match:
            return None

        command_name = match.group(1).lower()
        raw_args = match.group(2) or ""

        # 解析参数，支持引号
        args = self._parse_args(raw_args)

        command = Command(name=command_name, args=args, raw_args=raw_args, original_message=original_message)

        self.logger.debug(f"解析命令成功: {command_name}, 参数: {args}")
        return command

    def _parse_args(self, raw_args: str) -> list[str]:
        """
        解析参数字符串，支持引号。

        Args:
            raw_args: 原始参数字符串

        Returns:
            解析后的参数列表
        """
        if not raw_args.strip():
            return []

        try:
            # 使用shlex来正确处理引号
            args = shlex.split(raw_args)
            return args
        except ValueError as e:
            # 如果shlex解析失败（比如引号不匹配），回退到简单的空格分割
            self.logger.warning(f"参数解析失败，使用简单分割: {e}")
            return raw_args.split()

    def is_command(self, text: str) -> bool:
        """
        检查文本是否是命令。

        Args:
            text: 要检查的文本

        Returns:
            是否是命令
        """
        if not text or not text.strip():
            return False

        return bool(self.command_pattern.match(text.strip()))
