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

from typing import AsyncIterator
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
