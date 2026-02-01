"""
Mock Providers Plugin

模拟插件，为每一层提供模拟Provider：
- 输入层：模拟弹幕输入（使用MockDanmakuProvider）
- 决策层：模拟AI决策（基于规则）
- 输出层：模拟TTS和字幕输出（控制台打印）

这个插件可以让系统在没有外部依赖的情况下运行，用于测试和演示。
"""

import asyncio
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.event_bus import EventBus

from src.utils.logger import get_logger
from src.layers.input.providers.mock_danmaku_provider import MockDanmakuProvider
from .providers import MockDecisionProvider, MockTTSProvider, MockSubtitleProvider


class MockProvidersPlugin:
    """
    模拟Provider插件

    提供完整的输入、决策、输出三层模拟Provider，用于测试系统。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        # Provider列表
        self._providers: List[Any] = []

        # 数据采集任务
        self._collection_task: asyncio.Task = None
        self.event_bus: "EventBus" = None

        # 配置选项
        self.enabled = config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("MockProvidersPlugin 在配置中被禁用。")
            return

        # 要启用的Provider类型
        self.enable_input = config.get("enable_input", True)
        self.enable_decision = config.get("enable_decision", True)
        self.enable_output = config.get("enable_output", True)

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表
        """
        self.event_bus = event_bus
        self.logger.info("设置MockProvidersPlugin")

        if not self.enabled:
            return []

        # 1. 创建输入Provider（模拟弹幕）
        if self.enable_input:
            input_config = config.get("input", {})
            input_provider = MockDanmakuProvider(input_config)
            # InputProvider没有setup方法，只有start方法
            self._providers.append(input_provider)
            self.logger.info("✓ 已创建模拟弹幕输入Provider")

        # 2. 创建决策Provider（模拟AI）
        if self.enable_decision:
            decision_config = config.get("decision", {})
            decision_provider = MockDecisionProvider(decision_config)
            await decision_provider.setup(event_bus)
            self._providers.append(decision_provider)
            self.logger.info("✓ 已创建模拟决策Provider")

        # 3. 创建输出Provider（模拟TTS和字幕）
        if self.enable_output:
            output_config = config.get("output", {})

            # TTS Provider
            tts_config = output_config.get("tts", {})
            tts_provider = MockTTSProvider(tts_config)
            await tts_provider.setup(event_bus)
            self._providers.append(tts_provider)
            self.logger.info("✓ 已创建模拟TTS输出Provider")

            # Subtitle Provider
            subtitle_config = output_config.get("subtitle", {})
            subtitle_provider = MockSubtitleProvider(subtitle_config)
            await subtitle_provider.setup(event_bus)
            self._providers.append(subtitle_provider)
            self.logger.info("✓ 已创建模拟字幕输出Provider")

        self.logger.info(f"MockProvidersPlugin 设置完成，已创建 {len(self._providers)} 个Provider。")

        # 启动数据采集任务（仅输入Provider）
        if self.enable_input and config.get("start_immediately", True):
            input_provider = self._providers[0]  # 第一个应该是input provider
            self._collection_task = asyncio.create_task(self._run_collection_loop(input_provider))
            self.logger.info("✓ 模拟弹幕采集任务已启动")

        return self._providers

    async def _run_collection_loop(self, provider):
        """
        运行数据采集循环

        Args:
            provider: InputProvider实例
        """
        try:
            self.logger.info("模拟弹幕数据采集循环开始")

            async for raw_data in provider.start():
                # 通过EventBus发送原始数据
                await self.event_bus.emit(
                    "perception.raw_data.generated",
                    {"data": raw_data, "source": "mock_danmaku"},
                    source="MockProvidersPlugin",
                )

        except asyncio.CancelledError:
            self.logger.info("模拟弹幕数据采集循环被取消")
        except Exception as e:
            self.logger.error(f"模拟弹幕数据采集循环出错: {e}", exc_info=True)
        finally:
            self.logger.info("模拟弹幕数据采集循环结束")

    async def cleanup(self):
        """清理资源"""
        self.logger.info("清理MockProvidersPlugin...")

        # 取消采集任务
        if self._collection_task and not self._collection_task.done():
            self._collection_task.cancel()
            try:
                await self._collection_task
            except asyncio.CancelledError:
                pass

        # 清理所有Provider
        for provider in self._providers:
            await provider.cleanup()
        self._providers.clear()

        self.logger.info("MockProvidersPlugin清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "MockProviders",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "模拟插件，提供输入、决策、输出三层的模拟Provider，用于测试和演示",
            "category": "testing",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = MockProvidersPlugin
