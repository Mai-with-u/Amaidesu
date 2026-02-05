import json
import os
from typing import Dict, Optional, List, Iterator, Any

from maim_message import MessageBase
from src.domains.input.pipelines.manager import MessagePipeline
from src.core.utils.logger import get_logger

# 为静态方法准备一个模块级 logger
static_logger = get_logger("MessageLoggerPipelineUtils")


class MessageLoggerPipeline(MessagePipeline):
    """
    消息日志管道，将所有消息保存到JSON文件中。

    功能：
    1. 将消息保存到JSON文件中，每条消息一行（JSON Lines格式）
    2. 根据群组ID将消息分开保存到不同的文件
    """

    priority = 900  # 设置较低的优先级，让其他处理管道先处理消息

    def __init__(self, config: Dict[str, Any]):
        """
        初始化消息日志管道。

        Args:
            config: 合并后的配置字典，期望包含以下键:
                logs_dir (str): 日志文件存储目录 (默认: "logs/messages")
                file_prefix (str): 日志文件名前缀 (默认: "messages_")
                file_extension (str): 日志文件扩展名 (默认: ".jsonl")
                default_group_id (str): 当没有群组ID时使用的默认ID (默认: "default")
        """
        super().__init__(config)

        # 从配置中读取参数，如果未提供则使用默认值
        self._logs_dir = self.config.get("logs_dir", "logs/messages")
        self._file_prefix = self.config.get("file_prefix", "messages_")
        self._file_extension = self.config.get("file_extension", ".jsonl")
        self._default_group_id = self.config.get("default_group_id", "default")

        # 确保日志目录存在
        os.makedirs(self._logs_dir, exist_ok=True)

        # 记录已打开的文件句柄
        self._file_handles: Dict[str, object] = {}

        self.logger.info(f"消息日志管道初始化: 日志目录={self._logs_dir}")

    async def on_connect(self) -> None:
        """连接建立时确保日志目录存在"""
        os.makedirs(self._logs_dir, exist_ok=True)
        self.logger.info("消息日志管道已确认日志目录存在")

    async def on_disconnect(self) -> None:
        """连接断开时关闭所有打开的文件句柄"""
        for group_id, file_handle in self._file_handles.items():
            try:
                file_handle.close()
                self.logger.debug(f"已关闭群组 {group_id} 的日志文件")
            except Exception as e:
                self.logger.error(f"关闭群组 {group_id} 的日志文件时出错: {e}")

        self._file_handles.clear()
        self.logger.info("消息日志管道已关闭所有文件句柄")

    def _get_file_path(self, group_id: str) -> str:
        """
        获取指定群组的日志文件路径。

        Args:
            group_id: 群组ID

        Returns:
            日志文件的完整路径
        """
        # 替换文件名中的非法字符
        safe_group_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in group_id)
        file_name = f"{self._file_prefix}{safe_group_id}{self._file_extension}"
        return os.path.join(self._logs_dir, file_name)

    def _get_file_handle(self, group_id: str):
        """
        获取指定群组的文件句柄，如果不存在则创建。

        Args:
            group_id: 群组ID

        Returns:
            文件句柄
        """
        if group_id not in self._file_handles or self._file_handles[group_id].closed:
            file_path = self._get_file_path(group_id)
            self._file_handles[group_id] = open(file_path, "a", encoding="utf-8")
            self.logger.debug(f"已打开群组 {group_id} 的日志文件: {file_path}")

        return self._file_handles[group_id]

    def _format_message_for_log(self, message: MessageBase) -> dict:
        """
        将消息格式化为适合日志记录的字典。

        Args:
            message: 要格式化的消息对象

        Returns:
            格式化后的消息字典
        """
        return message.to_dict()

    def _write_message_to_log(self, message: MessageBase) -> None:
        """
        将消息写入日志文件。

        Args:
            message: 要记录的消息对象
        """
        try:
            # 确定群组ID
            group_id = self._default_group_id
            if message.message_info and message.message_info.group_info and message.message_info.group_info.group_id:
                group_id = message.message_info.group_info.group_id

            # 获取文件句柄
            file_handle = self._get_file_handle(group_id)

            # 格式化消息并写入
            formatted_message = self._format_message_for_log(message)
            file_handle.write(json.dumps(formatted_message, ensure_ascii=False) + "\n")
            file_handle.flush()  # 确保立即写入磁盘

            self.logger.debug(
                f"已记录消息: 群组={group_id}, 用户={formatted_message.get('user', {}).get('nickname', 'unknown')}, "
                f"类型={formatted_message.get('content_type', 'unknown')}"
            )
        except Exception as e:
            self.logger.error(f"记录消息到日志文件时出错: {e}", exc_info=True)

    async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
        """
        处理消息，将其记录到相应的日志文件中。

        Args:
            message: 要处理的消息对象

        Returns:
            原始消息对象，不做修改也不丢弃
        """
        # 记录消息到日志文件
        self._write_message_to_log(message)

        # 返回原始消息，允许继续处理
        return message

    @staticmethod
    def read_jsonl_file(file_path: str) -> List[dict]:
        """
        读取JSONL文件并返回所有消息的列表。

        Args:
            file_path: JSONL文件路径

        Returns:
            消息对象列表
        """
        messages = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():  # 忽略空行
                        messages.append(json.loads(line))
        except Exception as e:
            static_logger.error(f"读取JSONL文件 {file_path} 时出错: {e}")
        return messages

    @staticmethod
    def stream_jsonl_file(file_path: str) -> Iterator[dict]:
        """
        流式读取JSONL文件，每次返回一条消息，适用于处理大文件。

        Args:
            file_path: JSONL文件路径

        Returns:
            消息对象迭代器
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():  # 忽略空行
                        yield json.loads(line)
        except Exception as e:
            static_logger.error(f"流式读取JSONL文件 {file_path} 时出错: {e}")

    @staticmethod
    def extract_messages_by_user(file_path: str, user_id: str) -> List[dict]:
        """
        从JSONL文件中提取指定用户的所有消息。

        Args:
            file_path: JSONL文件路径
            user_id: 用户ID

        Returns:
            指定用户的消息列表
        """
        messages = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():  # 忽略空行
                        continue

                    try:
                        msg = json.loads(line)
                        # 兼容新旧两种格式
                        if "message_info" in msg and "user_info" in msg["message_info"]:
                            if msg["message_info"]["user_info"].get("user_id") == user_id:
                                messages.append(msg)
                        elif "user" in msg and msg["user"].get("user_id") == user_id:
                            messages.append(msg)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            static_logger.error(f"从JSONL文件 {file_path} 提取用户 {user_id} 的消息时出错: {e}")
        return messages
