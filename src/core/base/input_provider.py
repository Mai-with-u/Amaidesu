"""
输入Provider接口

定义了输入域(Input Domain)的Provider接口。
InputProvider负责从外部数据源采集原始数据。

示例实现：
- BilibiliDanmakuProvider: 从B站弹幕采集数据
- MinecraftProvider: 从Minecraft服务器采集游戏事件
- ConsoleProvider: 从控制台采集用户输入
- VoiceProvider: 从麦克风采集语音数据
"""

from typing import AsyncIterator, Dict, Any
from abc import ABC, abstractmethod

from src.core.base.raw_data import RawData


class InputProvider(ABC):
    """
    输入Provider抽象基类

    职责: 从外部数据源采集原始数据

    生命周期:
    1. 实例化(__init__)
    2. 启动(start()) - 返回异步生成器,产生RawData
    3. 停止(stop()) - 清理资源

    注意: InputProvider 使用 start()/stop() 而非 setup()/cleanup()
    原因: InputProvider 是异步数据流生成器,需要返回 AsyncIterator
    而 Decision/OutputProvider 是事件订阅者,使用 setup() 注册到 EventBus

    Attributes:
        config: Provider配置(来自新配置格式)
        is_running: 是否正在运行
    """

    def __init__(self, config: dict):
        """
        初始化Provider

        Args:
            config: Provider配置(来自perception.inputs.xxx配置)
        """
        self.config = config
        self.is_running = False

    async def start(self) -> AsyncIterator[RawData]:
        """
        启动Provider并返回数据流

        此方法是一个异步生成器,会持续产生RawData,
        直到被调用者取消或stop()被调用。

        Yields:
            RawData: 原始数据

        Raises:
            ConnectionError: 如果无法连接到数据源
        """
        self.is_running = True
        try:
            async for data in self._collect_data():
                yield data
        finally:
            self.is_running = False

    async def stop(self):
        """
        停止Provider

        停止数据采集并清理资源。
        """
        self.is_running = False
        await self._cleanup()

    async def cleanup(self):
        """
        清理资源（公开方法，供外部调用）

        此方法供 InputProviderManager 等外部管理器调用。
        默认调用内部的 _cleanup() 方法。

        子类可以重写 _cleanup() 方法来实现自定义清理逻辑。
        """
        await self._cleanup()

    @abstractmethod
    async def _collect_data(self) -> AsyncIterator[RawData]:
        """
        采集数据(子类必须实现)

        子类应该实现此方法来定义如何从数据源采集数据。

        Yields:
            RawData: 原始数据
        """
        pass

    async def _cleanup(self):  # noqa: B027
        """
        清理资源(子类可选重写)

        子类可以重写此方法来执行清理逻辑,
        如关闭连接、释放文件句柄等。
        """
        pass

    async def setup(self, event_bus=None, dependencies=None):  # noqa: B027
        """
        setup() 别名 - 为了与其他 Provider 保持一致

        注意: InputProvider 的 setup() 不返回 AsyncIterator,
        如果需要数据流,必须使用 start() 方法。

        此方法是为了接口一致性提供的空实现。
        InputProvider 不需要 setup(),因为它是数据生产者而非事件订阅者。

        Args:
            event_bus: EventBus实例(未使用,仅为了接口一致性)
            dependencies: 可选的依赖注入(未使用,仅为了接口一致性)
        """
        # InputProvider 不需要 setup(),因为它是数据生产者
        # 这个方法只是为了接口一致性
        pass

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """
        获取 Provider 注册信息（子类重写）

        用于显式注册模式，避免模块导入时的自动注册。

        Returns:
            注册信息字典，包含:
            - layer: "input"
            - name: Provider 名称（唯一标识符）
            - class: Provider 类
            - source: 注册来源（如 "builtin:console_input"）

        Raises:
            NotImplementedError: 如果子类未实现此方法

        Example:
            @classmethod
            def get_registration_info(cls):
                return {
                    "layer": "input",
                    "name": "console_input",
                    "class": cls,
                    "source": "builtin:console_input"
                }
        """
        raise NotImplementedError(
            f"{cls.__name__} 必须实现 get_registration_info() 类方法以支持显式注册。"
            "如果使用自动注册模式，可以在 __init__.py 中直接调用 ProviderRegistry.register_input()。"
        )
