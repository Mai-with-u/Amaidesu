"""
Maicraft插件Extension包装

将MaicraftPlugin包装为Extension，保持原有功能不变。
基于抽象工厂模式的弹幕互动游戏插件。
"""

from typing import Any, Dict, List
from src.core.extensions import BaseExtension, ExtensionInfo


class CoreWrapper:
    """
    AmaidesuCore包装器，提供插件需要的API

    这个包装器模拟AmaidesuCore的核心方法，将它们映射到EventBus或服务系统。
    MaicraftPlugin需要WebSocket处理器注册、服务注册和事件监听功能。
    """

    def __init__(self, event_bus: Any, platform: str = "amaidesu"):
        self.event_bus = event_bus
        self.platform = platform
        self._services = {}

    async def send_to_maicore(self, message: Any) -> None:
        """
        发送消息到MaiCore

        在新架构中，这个方法通过EventBus发布input.raw_data事件
        """
        await self.event_bus.emit("input.raw_data", message, "maicraft_extension")

    async def register_websocket_handler(self, msg_type: str, handler: Any) -> None:
        """
        注册WebSocket处理器

        在新架构中，这个方法映射到EventBus事件监听
        """
        self.event_bus.listen_event(f"websocket.{msg_type}", handler)

    def register_service(self, service_name: str, service: Any) -> None:
        """
        注册服务

        MaicraftPlugin注册maicraft_command_handler服务供其他插件使用
        """
        self._services[service_name] = service

    def get_service(self, service_name: str) -> Any:
        """
        获取服务

        MaicraftPlugin不需要获取其他服务
        """
        return self._services.get(service_name)

    def listen_event(self, event_name: str, handler: Any) -> None:
        """
        监听事件

        在新架构中，这个方法映射到EventBus事件监听
        """
        self.event_bus.listen_event(event_name, handler)


class MaicraftExtension(BaseExtension):
    """
    Maicraft插件Extension

    包装MaicraftPlugin，将其作为Extension加载。
    基于抽象工厂模式的弹幕互动游戏插件。
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._plugin = None
        self._core_wrapper = None

    async def setup(self, event_bus: Any, config: Dict[str, Any]) -> List[Any]:
        """
        设置Extension

        1. 创建CoreWrapper
        2. 延迟导入MaicraftPlugin
        3. 创建插件实例
        4. 调用插件的setup
        """
        self._event_bus = event_bus
        self.config = config

        # 创建CoreWrapper，提供插件需要的API
        self._core_wrapper = CoreWrapper(event_bus)

        # 延迟导入插件，避免循环依赖
        from src.plugins.maicraft.plugin import MaicraftPlugin

        # 创建插件实例
        self._plugin = MaicraftPlugin(self._core_wrapper, config)
        self.logger.info("MaicraftPlugin实例创建完成")

        # 调用插件的setup
        await self._plugin.setup()
        self.logger.info("MaicraftExtension设置完成")

        # 返回Provider列表（这个Extension没有Provider）
        return self._providers

    async def cleanup(self) -> None:
        """
        清理Extension
        """
        self.logger.info("MaicraftExtension清理中...")

        if self._plugin:
            await self._plugin.cleanup()
            self._plugin = None

        # 调用父类的cleanup
        await super().cleanup()

        self.logger.info("MaicraftExtension清理完成")

    def get_info(self) -> ExtensionInfo:
        """
        获取Extension信息
        """
        return ExtensionInfo(
            name="maicraft",
            version="1.0.0",
            description="Maicraft插件Extension包装（抽象工厂模式的弹幕互动游戏）",
            author="Amaidesu Team",
            dependencies=self.get_dependencies(),
            providers=self._providers,
            enabled=self.config.get("enabled", True),
        )

    def get_dependencies(self) -> List[str]:
        """
        获取依赖的Extension列表

        这个Extension不依赖其他Extension
        """
        return []


# Extension入口点 - ExtensionManager会查找这个变量
extension_class = MaicraftExtension
