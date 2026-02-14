"""
Mock Danmaku Input Provider

模拟弹幕输入Provider，从JSONL文件读取消息并直接构造 NormalizedMessage。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Literal

if TYPE_CHECKING:
    from src.modules.di.context import ProviderContext

from pydantic import Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.normalized_message import NormalizedMessage


class MockDanmakuInputProvider(InputProvider):
    """
    模拟弹幕输入Provider（重构后）

    从JSONL文件读取消息并按设定速率发送。
    直接构造 NormalizedMessage，无需中间数据结构。
    """

    class ConfigSchema(BaseProviderConfig):
        """模拟弹幕输入Provider配置"""

        type: Literal["mock_danmaku"] = "mock_danmaku"
        log_file_path: str = Field(default="msg_default.jsonl", description="日志文件路径")
        send_interval: float = Field(default=1.0, description="发送间隔（秒）", ge=0.1)
        loop_playback: bool = Field(default=True, description="循环播放")
        start_immediately: bool = Field(default=True, description="立即开始")

    def __init__(self, config: dict, context: "ProviderContext" = None):
        super().__init__(config, context)
        self.logger = get_logger("MockDanmakuInputProvider")

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # 从类型安全的配置对象读取参数
        self.log_filename = self.typed_config.log_file_path
        self.send_interval = max(0.1, self.typed_config.send_interval)
        self.loop_playback = self.typed_config.loop_playback
        self.start_immediately = self.typed_config.start_immediately

        # 获取 Provider 目录
        provider_dir = Path(__file__).resolve().parent
        self.data_dir = provider_dir / "data"

        # 确保data目录存在
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.logger.error(f"创建数据目录失败: {self.data_dir}: {e}")

        # 最终的日志文件路径
        self.log_file_path = self.data_dir / self.log_filename

        # 状态变量
        self._message_lines: list = []
        self._current_line_index: int = 0
        self._stop_event = asyncio.Event()

    async def generate(self) -> AsyncIterator[NormalizedMessage]:
        """
        启动模拟弹幕发送循环，直接返回 NormalizedMessage 流

        Yields:
            NormalizedMessage: 标准化消息
        """
        self.is_running = True

        try:
            # 加载消息
            await self._load_message_lines()

            if not self._message_lines:
                self.logger.warning(f"未从 '{self.log_file_path}' 加载任何消息。")
                return

            self.logger.info(f"模拟弹幕发送循环开始 (源: {self.log_file_path.name})")

            while not self._stop_event.is_set():
                if not self._message_lines:
                    self.logger.warning("消息列表为空，停止发送循环。")
                    break

                # 检查是否到达文件末尾
                if self._current_line_index >= len(self._message_lines):
                    if self.loop_playback:
                        self.logger.info("到达文件末尾，循环播放已启用，重置索引。")
                        self._current_line_index = 0
                    else:
                        self.logger.info("到达文件末尾，循环播放已禁用，停止发送。")
                        break

                # 重置后再次检查索引
                if self._current_line_index >= len(self._message_lines):
                    self.logger.warning("索引仍然超出范围，停止循环。")
                    break

                line = self._message_lines[self._current_line_index]
                self._current_line_index += 1

                try:
                    # 解析JSON
                    data = json.loads(line)

                    # 直接构造 NormalizedMessage
                    text = data.get("text", "")
                    user = data.get("user", "未知用户")
                    user_id = data.get("user_id", "")

                    message = NormalizedMessage(
                        text=text,
                        source="mock_danmaku",
                        data_type="text",
                        importance=0.5,
                        raw={
                            "user": user,
                            "user_id": user_id,
                        },
                    )

                    self.logger.debug(f"发送模拟消息 (行 {self._current_line_index}): {str(data)[:50]}...")

                    # 返回消息
                    yield message

                    # 等待间隔时间
                    await asyncio.sleep(self.send_interval)

                except asyncio.CancelledError:
                    self.logger.info("模拟弹幕发送循环被取消。")
                    break
                except json.JSONDecodeError as e:
                    self.logger.error(f"JSON 解析错误: {e}. 行内容: {line[:100]}...")
                except Exception as e:
                    self.logger.error(f"发送模拟消息时发生错误: {e}", exc_info=True)

            self.logger.info("模拟弹幕发送循环已结束。")

        finally:
            self.is_running = False

    async def _load_message_lines(self):
        """从 JSONL 文件加载消息行。"""
        self._message_lines = []
        self._current_line_index = 0

        if not self.log_file_path.exists() or not self.log_file_path.is_file():
            self.logger.error(f"日志文件未找到或不是文件: {self.log_file_path}")
            return

        try:
            with open(self.log_file_path, "r", encoding="utf-8") as f:
                self._message_lines = [line.strip() for line in f if line.strip()]
            self.logger.info(f"成功从 '{self.log_file_path.name}' 加载 {len(self._message_lines)} 行消息。")
        except Exception as e:
            self.logger.error(f"读取日志文件时出错: {self.log_file_path}: {e}", exc_info=True)
            self._message_lines = []

    async def cleanup(self):
        """清理资源"""
        self._stop_event.set()
        self._message_lines = []
        self._current_line_index = 0
