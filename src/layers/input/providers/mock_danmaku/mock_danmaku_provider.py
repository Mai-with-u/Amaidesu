"""
Mock Danmaku Input Provider

模拟弹幕输入Provider，从JSONL文件读取消息并发送到EventBus。
"""

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from src.core.base.input_provider import InputProvider
from src.core.base.raw_data import RawData
from src.utils.logger import get_logger


class MockDanmakuInputProvider(InputProvider):
    """
    模拟弹幕输入Provider

    从JSONL文件读取消息并按设定速率发送到EventBus。
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("MockDanmakuInputProvider")

        # 从配置中读取参数
        self.log_filename = self.config.get("log_file_path", "msg_default.jsonl")
        self.send_interval = max(0.1, self.config.get("send_interval", 1.0))
        self.loop_playback = self.config.get("loop_playback", True)
        self.start_immediately = self.config.get("start_immediately", True)

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

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """
        从JSONL文件采集数据

        Yields:
            RawData: 包含JSON消息的原始数据
        """
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

                # 创建RawData
                raw_data = RawData(
                    content=data, source="mock_danmaku", data_type="message", timestamp=asyncio.get_event_loop().time()
                )

                self.logger.debug(f"发送模拟消息 (行 {self._current_line_index}): {str(data)[:50]}...")

                # 返回数据
                yield raw_data

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

    async def _cleanup(self):
        """清理资源"""
        self._stop_event.set()
        self._message_lines = []
        self._current_line_index = 0
