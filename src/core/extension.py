"""
扩展系统 - Extension接口定义

Extension是Amaidesu的扩展机制，用于聚合多个Provider，
提供复杂功能的插件化支持。
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from .event_bus import EventBus


@dataclass
class ExtensionInfo:
    """
    扩展信息数据类

    Attributes:
        name: 扩展名称
        version: 扩展版本
        description: 扩展描述
        author: 扩展作者
        dependencies: 依赖的其他扩展名称列表
        providers: 扩展管理的Provider列表
        enabled: 是否启用
    """

    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    dependencies: List[str] = field(default_factory=list)
    providers: List[Any] = field(default_factory=list)  # List[Union[InputProvider, OutputProvider]]
    enabled: bool = True


class Extension(Protocol):
    """
    Extension协议（接口）

    Extension是聚合多个Provider的高级组件，用于实现复杂功能。
    与BasePlugin不同，Extension专注于Provider聚合和生命周期管理。

    Extension的职责：
    1. 管理多个Provider的生命周期（创建、启动、停止、清理）
    2. 提供扩展级别的配置和服务
    3. 通过EventBus与其他组件通信
    4. 可选地提供服务（通过Core的服务注册机制）
    """

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]:
        """
        初始化Extension

        在此方法中，Extension应该：
        1. 创建所需的Provider实例
        2. 订阅EventBus事件
        3. 可选地注册服务到Core（使用event_bus或直接注册）
        4. 执行任何初始化逻辑

        Args:
            event_bus: 事件总线实例，用于发布/订阅事件
            config: 扩展配置字典（来自config.toml的[extensions.xxx]）

        Returns:
            Provider列表: Extension管理的Provider实例列表

        Raises:
            RuntimeError: 如果初始化失败

        Example:
            >>> async def setup(self, event_bus, config):
            ...     # 创建Provider
            ...     provider = MyProvider(config)
            ...
            ...     # 订阅事件
            ...     event_bus.on("some.event", self.on_event)
            ...
            ...     # 返回Provider列表
            ...     return [provider]
        """
        ...

    async def cleanup(self) -> None:
        """
        清理Extension资源

        在此方法中，Extension应该：
        1. 取消所有EventBus事件订阅
        2. 停止所有Provider
        3. 清理任何持有的资源（网络连接、文件句柄等）
        4. 注销服务（如果注册过）

        Raises:
            Exception: 如果清理失败
        """
        ...

    def get_info(self) -> ExtensionInfo:
        """
        获取扩展信息

        Returns:
            ExtensionInfo: 扩展的信息对象
        """
        ...

    def get_dependencies(self) -> List[str]:
        """
        获取依赖的其他扩展名称

        返回此扩展依赖的其他扩展的名称列表。
        ExtensionManager将根据依赖关系按顺序加载扩展。

        Returns:
            List[str]: 依赖的扩展名称列表

        Example:
            >>> def get_dependencies(self):
            ...     return ["tts", "vts"]  # 依赖tts和vts扩展
        """
        ...


class BaseExtension:
    """
    Extension基类（可选）

    提供Extension的默认实现，子类可以继承并重写需要的方法。

    基类提供了：
    - 事件订阅/取消订阅的便捷方法
    - Provider管理
    - 基本的日志记录
    - 资源清理
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化Extension

        Args:
            config: 扩展配置
        """
        from src.utils.logger import get_logger

        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self._event_bus: Optional["EventBus"] = None
        self._providers: List[Any] = []  # List[Union[InputProvider, OutputProvider]]
        self._is_setup = False

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]:
        """
        默认实现（子类通常需要重写）

        Args:
            event_bus: 事件总线
            config: 扩展配置

        Returns:
            Provider列表
        """
        self._event_bus = event_bus
        self.config = config
        self._is_setup = True
        self.logger.info(f"Extension设置完成: {self.__class__.__name__}")
        return self._providers

    async def cleanup(self) -> None:
        """
        默认实现（子类可以重写）

        清理所有Provider和事件订阅
        """
        # 清理Provider
        for provider in self._providers:
            if hasattr(provider, "cleanup"):
                try:
                    await provider.cleanup()
                except Exception as e:
                    self.logger.error(f"清理Provider失败: {provider.__class__.__name__}: {e}")

        self._providers.clear()
        self._event_bus = None
        self._is_setup = False
        self.logger.info(f"Extension清理完成: {self.__class__.__name__}")

    def get_info(self) -> ExtensionInfo:
        """
        默认实现（子类可以重写）

        Returns:
            ExtensionInfo对象
        """
        return ExtensionInfo(
            name=self.__class__.__name__,
            description="",
            author="",
            dependencies=self.get_dependencies(),
            providers=self._providers,
        )

    def get_dependencies(self) -> List[str]:
        """
        默认实现（子类可以重写）

        Returns:
            空列表
        """
        return []

    def add_provider(self, provider: Any) -> None:
        """
        添加Provider到Extension

        Args:
            provider: Provider实例
        """
        self._providers.append(provider)
        self.logger.debug(f"添加Provider: {provider.__class__.__name__}")

    def remove_provider(self, provider: Any) -> None:
        """
        从Extension移除Provider

        Args:
            provider: Provider实例
        """
        if provider in self._providers:
            self._providers.remove(provider)
            self.logger.debug(f"移除Provider: {provider.__class__.__name__}")

    async def emit_event(self, event_name: str, data: Any) -> None:
        """
        发布事件（便捷方法）

        Args:
            event_name: 事件名称
            data: 事件数据
        """
        if self._event_bus:
            await self._event_bus.emit(event_name, data, self.__class__.__name__)
        else:
            self.logger.warning(f"EventBus未初始化，无法发布事件: {event_name}")

    def listen_event(self, event_name: str, handler: Any) -> None:
        """
        订阅事件（便捷方法）

        Args:
            event_name: 事件名称
            handler: 事件处理器
        """
        if self._event_bus:
            self._event_bus.on(event_name, handler)
        else:
            self.logger.warning(f"EventBus未初始化，无法订阅事件: {event_name}")

    def stop_listening_event(self, event_name: str, handler: Any) -> None:
        """
        取消订阅事件（便捷方法）

        Args:
            event_name: 事件名称
            handler: 事件处理器
        """
        if self._event_bus:
            self._event_bus.off(event_name, handler)
        else:
            self.logger.warning(f"EventBus未初始化，无法取消订阅事件: {event_name}")
