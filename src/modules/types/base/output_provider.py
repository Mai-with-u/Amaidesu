"""
输出Provider接口

定义了输出域（Output Domain）的Provider接口。
OutputProvider负责将RenderParameters渲染到目标设备。

示例实现:
- TTSProvider: 将文本转换为语音并播放
- SubtitleProvider: 在窗口中显示字幕
- VTSProvider: 控制VTube Studio虚拟形象
- StickerProvider: 显示表情贴纸
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from src.domains.output.parameters.render_parameters import RenderParameters


class OutputProvider(ABC):
    """
    输出Provider抽象基类

    职责: 将渲染参数输出到目标设备

    生命周期:
    1. 实例化(__init__)
    2. 设置(setup()) - 注册到EventBus
    3. 渲染(render()) - 执行渲染
    4. 清理(cleanup()) - 释放资源

    Attributes:
        config: Provider配置(来自rendering.outputs.xxx配置)
        event_bus: EventBus实例(可选,用于事件通信)
        is_setup: 是否已完成设置
    """

    def __init__(self, config: dict):
        """
        初始化Provider

        Args:
            config: Provider配置(来自rendering.outputs.xxx配置)
        """
        self.config = config
        self.event_bus = None
        self.is_setup = False

    async def setup(self, event_bus, dependencies: Optional[Dict[str, Any]] = None):
        """
        设置Provider

        Args:
            event_bus: EventBus实例
            dependencies: 可选的依赖注入（替代 core）

        Raises:
            ConnectionError: 如果无法连接到目标设备
        """
        self.event_bus = event_bus
        self._dependencies = dependencies or {}
        await self._setup_internal()
        self.is_setup = True

    async def render(self, parameters: "RenderParameters"):
        """
        渲染参数

        Args:
            parameters: 渲染参数

        Raises:
            RuntimeError: 如果Provider未设置
            Exception: 渲染过程中的错误
        """
        if not self.is_setup:
            raise RuntimeError("Provider not setup, call setup() first")
        await self._render_internal(parameters)

    async def _setup_internal(self):  # noqa: B027
        """内部设置逻辑(子类可选重写)"""
        # 子类可以重写此方法来执行初始化逻辑,如连接到设备、加载资源等。
        ...

    @abstractmethod
    async def _render_internal(self, parameters: "RenderParameters"):
        """
        内部渲染逻辑(子类必须实现)

        子类必须实现此方法来定义如何渲染参数。

        Args:
            parameters: 渲染参数
        """
        pass

    async def cleanup(self):
        """
        清理资源

        停止Provider并释放所有资源。
        """
        await self._cleanup_internal()
        self.is_setup = False

    def get_info(self) -> Dict[str, Any]:
        """
        获取Provider信息

        Returns:
            Provider信息字典，包含:
            - name: Provider名称
            - is_setup: 是否已完成设置
            - type: Provider类型
        """
        return {
            "name": self.__class__.__name__,
            "is_setup": self.is_setup,
            "type": "output_provider",
        }

    async def _cleanup_internal(self):  # noqa: B027
        """内部清理逻辑(子类可选重写)"""
        # 子类可以重写此方法来执行清理逻辑,如关闭连接、释放资源等。
        ...

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """
        获取 Provider 注册信息（子类重写）

        用于显式注册模式，避免模块导入时的自动注册。

        Returns:
            注册信息字典，包含:
            - layer: "output"
            - name: Provider 名称（唯一标识符）
            - class: Provider 类
            - source: 注册来源（如 "builtin:subtitle"）

        Raises:
            NotImplementedError: 如果子类未实现此方法

        Example:
            @classmethod
            def get_registration_info(cls):
                return {
                    "layer": "output",
                    "name": "subtitle",
                    "class": cls,
                    "source": "builtin:subtitle"
                }
        """
        raise NotImplementedError(
            f"{cls.__name__} 必须实现 get_registration_info() 类方法以支持显式注册。"
            "如果使用自动注册模式，可以在 __init__.py 中直接调用 ProviderRegistry.register_output()。"
        )
