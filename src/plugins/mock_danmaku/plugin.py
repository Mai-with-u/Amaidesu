"""
Mock Danmaku Plugin

模拟弹幕插件，从JSONL文件读取消息并按设定速率发送。
迁移到新的Plugin架构。
"""

import asyncio
from typing import Dict, Any, List

from src.core.event_bus import EventBus
from src.plugins.mock_danmaku.mock_danmaku_input_provider import MockDanmakuInputProvider
from src.utils.logger import get_logger


class MockDanmakuPlugin:
    """
    模拟弹幕插件

    使用InputProvider从JSONL文件读取消息并通过EventBus发送。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        # Provider列表
        self._providers: List[MockDanmakuInputProvider] = []

        # 数据采集任务
        self._collection_task: asyncio.Task = None
        self.event_bus: EventBus = None

        if not self.config.get("enabled", True):
            self.logger.warning("MockDanmakuPlugin 在配置中被禁用。")
            return

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表
        """
        self.event_bus = event_bus
        self.logger.info("设置MockDanmakuPlugin")

        if not self.config.get("enabled", True):
            return []

        # 创建InputProvider
        input_provider = MockDanmakuInputProvider(config)
        await input_provider.setup(event_bus, config)
        self._providers.append(input_provider)

        self.logger.info(f"MockDanmakuPlugin 设置完成，已创建 {len(self._providers)} 个Provider。")

        # 启动数据采集任务
        if config.get("start_immediately", True):
            self._collection_task = asyncio.create_task(self._run_collection_loop(input_provider))
            self.logger.info("模拟弹幕采集任务已启动。")

        return self._providers

    async def _run_collection_loop(self, provider: MockDanmakuInputProvider):
        """
        运行数据采集循环

        Args:
            provider: InputProvider实例
        """
        try:
            self.logger.info("模拟弹幕数据采集循环开始。")

            async for raw_data in provider.start():
                # 通过EventBus发送原始数据（使用标准事件格式）
                await self.event_bus.emit(
                    "perception.raw_data.generated",
                    {"data": raw_data, "source": "mock_danmaku"},
                    source="MockDanmakuPlugin",
                )

        except asyncio.CancelledError:
            self.logger.info("模拟弹幕数据采集循环被取消。")
        except Exception as e:
            self.logger.error(f"模拟弹幕数据采集循环出错: {e}", exc_info=True)
        finally:
            self.logger.info("模拟弹幕数据采集循环结束。")

    async def cleanup(self):
        """清理资源"""
        self.logger.info("清理MockDanmakuPlugin...")

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

        self.logger.info("MockDanmakuPlugin清理完成。")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "MockDanmaku",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "模拟弹幕插件，从JSONL文件读取消息并按设定速率发送",
            "category": "input",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = MockDanmakuPlugin
