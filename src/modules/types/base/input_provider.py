"""
输入Provider接口

定义了输入域(Input Domain)的Provider接口。
InputProvider负责从外部数据源采集数据并直接构造 NormalizedMessage。

示例实现：
- BilibiliDanmakuProvider: 从B站弹幕采集数据
- MinecraftProvider: 从Minecraft服务器采集游戏事件
- ConsoleProvider: 从控制台采集用户输入
- VoiceProvider: 从麦克风采集语音数据
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict, Any

from src.modules.types.base.normalized_message import NormalizedMessage


class InputProvider(ABC):
    """
    输入Provider抽象基类

    职责: 从外部数据源采集数据并直接构造 NormalizedMessage

    生命周期:
    1. 实例化(__init__)
    2. 启动(start()) - 初始化资源配置，建立连接
    3. 获取数据(stream()) - 返回异步生成器，产生 NormalizedMessage
    4. 停止(stop()) - 调用 cleanup() 清理资源

    使用方式:
        # 启动 Provider
        await provider.start()

        # 获取数据流
        async for message in provider.stream():
            ...

        # 停止 Provider
        await provider.stop()

    公开方法:
    - init(): 初始化资源配置（子类可重写）
    - start(): 启动 Provider，建立连接
    - stream(): 获取 NormalizedMessage 数据流
    - stop(): 停止 Provider
    - cleanup(): 清理资源（子类可重写）
    - generate(): 生成 NormalizedMessage 数据流（子类必须实现）

    Attributes:
        config: Provider配置(来自新配置格式)
        is_running: 是否已启动
        _dependencies: 依赖注入字典
    """

    def __init__(self, config: dict):
        """
        初始化Provider

        Args:
            config: Provider配置(来自perception.inputs.xxx配置)
        """
        self.config = config
        self.is_running = False
        self._dependencies: Dict[str, Any] = {}

    async def init(self) -> None:  # noqa: B027
        """
        初始化资源配置（子类可重写）

        执行初始化逻辑，如建立连接、加载配置等。
        此方法在 start() 时调用。

        子类可以重写此方法来执行初始化逻辑，
        如建立连接、打开文件句柄等。
        """
        pass

    async def start(self) -> None:
        """
        启动 Provider，建立连接

        此方法负责：
        1. 调用 init() 进行初始化
        2. 建立与数据源的连接
        3. 设置 is_running = True

        使用方式:
            await provider.start()
            async for message in provider.stream():
                ...
        """
        await self.init()
        self.is_running = True

    def stream(self) -> AsyncIterator[NormalizedMessage]:
        """
        返回 NormalizedMessage 数据流

        这是一个异步生成器，调用后会迭代 Provider 产生的数据。

        注意: 调用此方法前必须先调用 start() 启动 Provider。

        使用方式:
            await provider.start()
            async for message in provider.stream():
                ...

        Yields:
            NormalizedMessage: 标准化消息

        Raises:
            RuntimeError: 如果 Provider 未启动
        """
        if not self.is_running:
            raise RuntimeError("Provider 未启动，请先调用 start()")

        async def _generate():
            try:
                async for message in self.generate():
                    yield message
            finally:
                self.is_running = False

        return _generate()

    async def stop(self):
        """
        停止 Provider

        停止数据采集并调用 cleanup() 清理资源。
        """
        self.is_running = False
        await self.cleanup()

    async def cleanup(self) -> None:  # noqa: B027
        """
        清理资源（子类可重写）

        执行清理逻辑，如关闭连接、释放资源等。
        此方法在 stop() 时调用。

        子类可以重写此方法来执行自定义清理逻辑。
        """
        pass

    @abstractmethod
    async def generate(self) -> AsyncIterator[NormalizedMessage]:
        """
        生成 NormalizedMessage 数据流（子类必须实现）

        子类必须实现此方法来定义数据生成逻辑。

        Yields:
            NormalizedMessage: 标准化消息
        """
        pass
