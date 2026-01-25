"""
示例Extension - 用于测试Extension系统

这个Extension演示如何创建一个基本的Extension，包括：
- 继承BaseExtension
- 实现get_info()方法
- 使用EventBus
- 管理Provider
"""

from typing import Any, Dict, List
from src.core.extensions import BaseExtension, ExtensionInfo


class ExampleExtension(BaseExtension):
    """
    示例Extension

    这个Extension不提供实际功能，仅用于演示Extension的基本结构。
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.example_config_value = config.get("example_key", "default_value")
        self.logger.info(f"ExampleExtension初始化，配置值: {self.example_config_value}")

    async def setup(self, event_bus: Any, config: Dict[str, Any]) -> List[Any]:
        """
        设置Extension

        在这个方法中，Extension应该：
        1. 订阅EventBus事件
        2. 创建Provider（如果需要）
        3. 执行初始化逻辑
        """
        self._event_bus = event_bus
        self.config = config
        self._is_setup = True

        self.logger.info("ExampleExtension设置完成")

        # 示例：订阅一个事件
        self.listen_event("test.event", self._on_test_event)

        # 返回空列表（这个Extension没有Provider）
        return self._providers

    async def cleanup(self) -> None:
        """
        清理Extension
        """
        self.logger.info("ExampleExtension清理中...")

        # 停止监听事件
        self.stop_listening_event("test.event", self._on_test_event)

        # 调用父类的cleanup
        await super().cleanup()

        self.logger.info("ExampleExtension清理完成")

    def get_info(self) -> ExtensionInfo:
        """
        获取Extension信息
        """
        return ExtensionInfo(
            name="example",
            version="1.0.0",
            description="示例Extension，用于演示Extension系统的基本功能",
            author="Amaidesu Team",
            dependencies=self.get_dependencies(),
            providers=self._providers,
            enabled=True,
        )

    def get_dependencies(self) -> List[str]:
        """
        获取依赖的Extension列表

        这个示例Extension不依赖任何其他Extension
        """
        return []

    async def _on_test_event(self, data: Any) -> None:
        """
        测试事件处理器

        这个方法在接收到"test.event"事件时被调用
        """
        self.logger.info(f"接收到test.event，数据: {data}")


# 扩展入口点 - ExtensionManager会查找这个变量
extension_class = ExampleExtension
