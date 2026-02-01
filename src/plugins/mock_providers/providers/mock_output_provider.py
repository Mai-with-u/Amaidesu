"""
Mock Output Providers - 模拟输出Provider

提供控制台打印的模拟TTS和字幕输出，用于测试输出层。
"""

import asyncio
from typing import Dict, Any, Optional

from src.core.base.output_provider import OutputProvider
from src.layers.parameters.render_parameters import RenderParameters
from src.utils.logger import get_logger


class MockTTSProvider(OutputProvider):
    """
    模拟TTS输出Provider

    将文本打印到控制台，模拟TTS输出。
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化MockTTSProvider

        Args:
            config: 配置字典
        """
        super().__init__(config)
        self.logger = get_logger("MockTTSProvider")

        # 读取配置
        self.speak_delay = config.get("speak_delay", 0.0)  # 模拟TTS播放延迟
        self.show_timestamp = config.get("show_timestamp", True)
        self.prefix = config.get("prefix", "🔊 TTS")

        self.logger.info("MockTTSProvider初始化完成")

    async def _setup_internal(self):
        """内部设置逻辑"""
        self.logger.info("MockTTSProvider设置完成")

    async def _render_internal(self, parameters: RenderParameters):
        """
        渲染参数

        Args:
            parameters: 渲染参数
        """
        # 提取TTS文本
        text = parameters.tts_text or ""

        if not text:
            self.logger.debug("TTS收到空文本，跳过")
            return

        # 检查是否启用TTS
        if not parameters.tts_enabled:
            self.logger.debug("TTS已禁用，跳过")
            return

        # 构建输出
        output_parts = []

        if self.show_timestamp:
            import time

            timestamp = time.strftime("%H:%M:%S")
            output_parts.append(f"[{timestamp}]")

        output_parts.append(f"{self.prefix}")
        output_parts.append(text)

        output = " ".join(output_parts)

        # 打印到控制台
        print(output)
        self.logger.info(f"TTS输出: {text}")

        # 模拟TTS播放延迟
        if self.speak_delay > 0:
            await asyncio.sleep(self.speak_delay)

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("MockTTSProvider清理完成")


class MockSubtitleProvider(OutputProvider):
    """
    模拟字幕输出Provider

    将字幕信息打印到控制台，模拟字幕窗口显示。
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化MockSubtitleProvider

        Args:
            config: 配置字典
        """
        super().__init__(config)
        self.logger = get_logger("MockSubtitleProvider")

        # 读取配置
        self.display_duration = config.get("display_duration", 3.0)  # 字幕显示时长
        self.show_border = config.get("show_border", True)
        self.border_char = config.get("border_char", "═")
        self.width = config.get("width", 60)

        self.logger.info("MockSubtitleProvider初始化完成")

    async def _setup_internal(self):
        """内部设置逻辑"""
        self.logger.info("MockSubtitleProvider设置完成")

    async def _render_internal(self, parameters: RenderParameters):
        """
        渲染参数

        Args:
            parameters: 渲染参数
        """
        # 提取字幕文本
        text = parameters.subtitle_text or ""

        if not text:
            self.logger.debug("字幕收到空文本，跳过")
            return

        # 检查是否启用字幕
        if not parameters.subtitle_enabled:
            self.logger.debug("字幕已禁用，跳过")
            return

        # 构建字幕框
        lines = []

        if self.show_border:
            border = self.border_char * self.width
            lines.append(border)

        # 文本居中显示
        text_width = len(text)
        if text_width <= self.width - 4:
            padding = (self.width - 2 - text_width) // 2
            centered_text = " " * padding + text + " " * (self.width - 2 - text_width - padding)
        else:
            centered_text = text[: self.width - 4] + ".."

        lines.append(f"║{centered_text}║")

        if self.show_border:
            lines.append(border)

        # 打印字幕框
        subtitle = "\n".join(lines)
        print(f"\n{subtitle}\n")
        self.logger.info(f"字幕输出: {text}")

        # 模拟字幕显示时长
        if self.display_duration > 0:
            await asyncio.sleep(self.display_duration)

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("MockSubtitleProvider清理完成")
