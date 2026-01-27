# src/plugins/vtube_studio/plugin.py

from typing import Dict, Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.event_bus import EventBus

from src.core.providers.output_provider import OutputProvider
from src.utils.logger import get_logger
from .providers.vts_output_provider import VTSOutputProvider


class VTubeStudioPlugin:
    """
    VTube Studio 虚拟形象控制插件

    迁移到新的Plugin接口：
    - 不继承BasePlugin
    - 实现Plugin协议
    - 返回Provider列表（VTSOutputProvider）
    - 保留服务注册接口以向后兼容
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus: Optional["EventBus"] = None
        self._providers: List[OutputProvider] = []
        self.vts_provider: Optional[VTSOutputProvider] = None

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("VTubeStudioPlugin 在配置中已禁用。")
            return

        # 依赖检查
        try:
            import pyvts  # noqa: F401
        except ImportError:
            self.logger.error("pyvts library not found. Please install it (`pip install pyvts`).")
            self.enabled = False
            return

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（包含VTSOutputProvider）
        """
        self.event_bus = event_bus

        if not self.enabled:
            self.logger.warning("VTubeStudioPlugin 未启用，不创建Provider。")
            return []

        # 创建Provider
        try:
            provider = VTSOutputProvider(self.config, event_bus)
            await provider.setup(event_bus)
            self._providers.append(provider)
            self.logger.info("VTSOutputProvider 已创建")

            # 注册服务（向后兼容）
            # 其他插件可以通过event_bus或服务注册获取VTS控制功能
            await self._register_services(provider)

        except Exception as e:
            self.logger.error(f"创建 VTSOutputProvider 失败: {e}", exc_info=True)
            return []

        return self._providers

    async def _register_services(self, provider: VTSOutputProvider):
        """注册服务以向后兼容"""
        # 在新架构中，服务注册通过EventBus进行
        # 这里保留一些关键的服务接口方法供其他插件使用

        # VTS控制服务
        self.vts_provider = provider

        # 将provider本身作为服务导出
        # 在新架构中，通过event_bus进行服务发现
        pass

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 VTubeStudioPlugin...")

        # 清理所有Provider
        for provider in self._providers:
            try:
                await provider.cleanup()
            except Exception as e:
                self.logger.error(f"清理 Provider 时出错: {e}", exc_info=True)

        self._providers.clear()
        self.logger.info("VTubeStudioPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "VTubeStudio",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "VTube Studio虚拟形象控制插件",
            "category": "output",
            "api_version": "1.0",
        }

    # --- 服务接口方法（向后兼容） ---

    async def trigger_hotkey(self, hotkey_id: str) -> bool:
        """触发热键"""
        if self.vts_provider:
            return await self.vts_provider.trigger_hotkey(hotkey_id)
        return False

    async def set_parameter_value(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        """设置参数值"""
        if self.vts_provider:
            return await self.vts_provider.set_parameter_value(parameter_name, value, weight)
        return False

    async def close_eyes(self) -> bool:
        """闭眼"""
        if self.vts_provider:
            return await self.vts_provider.close_eyes()
        return False

    async def open_eyes(self) -> bool:
        """睁眼"""
        if self.vts_provider:
            return await self.vts_provider.open_eyes()
        return False

    async def smile(self, value: float = 1) -> bool:
        """微笑"""
        if self.vts_provider:
            return await self.vts_provider.smile(value)
        return False


# --- Plugin Entry Point ---
plugin_entrypoint = VTubeStudioPlugin
