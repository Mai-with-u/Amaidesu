"""
MockDanmakuCollector - 模拟弹幕输入Collector

从JSONL文件读取消息并按设定速率发送模拟弹幕。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Literal

from pydantic import Field

from src.domains.input.registry import collector
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.types.base.normalized_message import NormalizedMessage


@collector("mock_danmaku")
class MockDanmakuCollector:
    """
    模拟弹幕输入Collector

    从JSONL文件读取消息并按设定速率发送。
    """

    class ConfigSchema(BaseProviderConfig):
        """模拟弹幕输入Collector配置"""

        type: Literal["mock_danmaku"] = "mock_danmaku"
        log_file_path: str = Field(default="msg_default.jsonl", description="日志文件路径")
        send_interval: float = Field(default=1.0, description="发送间隔（秒）", ge=0.1)
        loop_playback: bool = Field(default=True, description="循环播放")
        start_immediately: bool = Field(default=True, description="立即开始")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
    ):
        """
        初始化MockDanmakuCollector

        Args:
            config: 配置字典
            event_bus: 事件总线实例
        """
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema(**config)
        self.log_filename = self.typed_config.log_file_path
        self.send_interval = max(0.1, self.typed_config.send_interval)
        self.loop_playback = self.typed_config.loop_playback
        self.start_immediately = self.typed_config.start_immediately

        provider_dir = Path(__file__).resolve().parent
        self.data_dir = provider_dir / "data"

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.logger.error(f"创建数据目录失败: {self.data_dir}: {e}")

        self.log_file_path = self.data_dir / self.log_filename

        self._message_lines: list = []
        self._current_line_index: int = 0
        self._stop_event = asyncio.Event()
        self.is_started = False

    def stream(self) -> AsyncIterator[NormalizedMessage]:
        if not self.is_started:
            raise RuntimeError("Collector 未启动，请先调用 start()")

        async def _generate():
            try:
                async for message in self.collect():
                    yield message
            finally:
                self.is_started = False

        return _generate()

    async def start(self) -> None:
        self.is_started = True

    async def stop(self) -> None:
        self.is_started = False

    async def cleanup(self) -> None:
        self._stop_event.set()
        self._message_lines = []
        self._current_line_index = 0
        self.logger.info("MockDanmakuCollector 已清理")

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
        """启动模拟弹幕发送循环"""
        self.is_started = True

        try:
            await self._load_message_lines()

            if not self._message_lines:
                self.logger.warning(f"未从 '{self.log_file_path}' 加载任何消息。")
                return

            self.logger.info(f"模拟弹幕发送循环开始 (源: {self.log_file_path.name})")

            while not self._stop_event.is_set():
                if not self._message_lines:
                    self.logger.warning("消息列表为空，停止发送循环。")
                    break

                if self._current_line_index >= len(self._message_lines):
                    if self.loop_playback:
                        self.logger.info("到达文件末尾，循环播放已启用，重置索引。")
                        self._current_line_index = 0
                    else:
                        self.logger.info("到达文件末尾，循环播放已禁用，停止发送。")
                        break

                if self._current_line_index >= len(self._message_lines):
                    self.logger.warning("索引仍然超出范围，停止循环。")
                    break

                line = self._message_lines[self._current_line_index]
                self._current_line_index += 1

                try:
                    data = json.loads(line)

                    text = data.get("text", "")
                    user = data.get("user_name", "未知用户")
                    user_id = data.get("user_id", "")

                    message = NormalizedMessage(
                        text=text,
                        source="mock_danmaku",
                        data_type="text",
                        importance=0.5,
                        user_id=user_id or None,
                        user_nickname=user or None,
                        platform="mock",
                    )

                    self.logger.debug(f"发送模拟消息 (行 {self._current_line_index}): {str(data)[:50]}...")
                    yield message

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
            self.is_started = False

    async def _load_message_lines(self) -> None:
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
