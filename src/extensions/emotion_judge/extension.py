"""
Emotion Judge插件Extension包装

将EmotionJudgePlugin包装为Extension，保持原有功能不变。
支持根据文本内容自动判断情感状态并触发对应Live2D热键。
"""

from typing import Any, Dict, List
from src.core.extensions import BaseExtension, ExtensionInfo


class CoreWrapper:
    """
    AmaidesuCore包装器，提供插件需要的API

    这个包装器模拟AmaidesuCore的核心方法，将它们映射到EventBus或服务系统。
    EmotionJudgePlugin需要vts_control服务来触发热键。
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
        await self.event_bus.emit("input.raw_data", message, "emotion_judge_extension")

    async def register_websocket_handler(self, msg_type: str, handler: Any) -> None:
        """
        注册WebSocket处理器

        在新架构中，这个方法映射到EventBus事件监听
        """
        self.event_bus.listen_event(f"websocket.{msg_type}", handler)

    def register_service(self, service_name: str, service: Any) -> None:
        """
        注册服务

        EmotionJudgePlugin不注册服务
        """
        self._services[service_name] = service

    def get_service(self, service_name: str) -> Any:
        """
        获取服务

        EmotionJudgePlugin需要获取vts_control服务来触发热键
        """
        return self._services.get(service_name)


class EmotionJudgeExtension(BaseExtension):
    """
    Emotion Judge插件Extension

    包装EmotionJudgePlugin，将其作为Extension加载。
    支持根据文本内容自动判断情感状态并触发对应Live2D热键。
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._plugin = None
        self._core_wrapper = None

    async def setup(self, event_bus: Any, config: Dict[str, Any]) -> List[Any]:
        """
        设置Extension

        1. 创建CoreWrapper
        2. 延迟导入EmotionJudgePlugin
        3. 创建插件实例
        4. 调用插件的setup
        """
        self._event_bus = event_bus
        self.config = config

        # 创建CoreWrapper，提供插件需要的API
        self._core_wrapper = CoreWrapper(event_bus)

        # 延迟导入插件，避免循环依赖
        from src.plugins.emotion_judge.plugin import EmotionJudgePlugin

        # 创建插件实例
        self._plugin = EmotionJudgePlugin(self._core_wrapper, config)
        self.logger.info("EmotionJudgePlugin实例创建完成")

        # 调用插件的setup
        await self._plugin.setup()
        self.logger.info("EmotionJudgeExtension设置完成")

        # 返回Provider列表（这个Extension没有Provider）
        return self._providers

    async def cleanup(self) -> None:
        """
        清理Extension
        """
        self.logger.info("EmotionJudgeExtension清理中...")

        if self._plugin:
            await self._plugin.cleanup()
            self._plugin = None

        # 调用父类的cleanup
        await super().cleanup()

        self.logger.info("EmotionJudgeExtension清理完成")

    def get_info(self) -> ExtensionInfo:
        """
        获取Extension信息
        """
        return ExtensionInfo(
            name="emotion_judge",
            version="1.0.0",
            description="Emotion Judge插件Extension包装（LLM情感判断+热键触发）",
            author="Amaidesu Team",
            dependencies=self.get_dependencies(),
            providers=self._providers,
            enabled=self.config.get("enabled", True),
        )

    def get_dependencies(self) -> List[str]:
        """
        获取依赖的Extension列表

        这个Extension依赖vtube_studio extension来提供vts_control服务
        """
        return ["vtube_studio"]


# Extension入口点 - ExtensionManager会查找这个变量
extension_class = EmotionJudgeExtension
