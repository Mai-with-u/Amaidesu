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
    输入Provider抽象基类（重构后）

    职责: 从外部数据源采集数据并直接构造 NormalizedMessage

    数据流: start() → yield NormalizedMessage

    生命周期:
    1. 实例化(__init__)
    2. 启动(start()) - 返回异步生成器,产生 NormalizedMessage
    3. 停止(stop()) - 清理资源

    内部方法约定:
    - _setup_internal(): 内部初始化方法,在start()开始时调用
    - _cleanup_internal(): 内部清理方法,在stop()时调用
    - _cleanup(): _cleanup_internal()的别名,保持向后兼容

    Attributes:
        config: Provider配置(来自新配置格式)
        is_running: 是否正在运行
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

    @abstractmethod
    async def start(self) -> AsyncIterator[NormalizedMessage]:
        """
        启动 Provider 并返回 NormalizedMessage 流

        重构后：直接返回 NormalizedMessage，不再返回 RawData

        Yields:
            NormalizedMessage: 标准化消息

        Raises:
            ConnectionError: 如果无法连接到数据源

        Note:
            子类必须实现此方法，直接构造 NormalizedMessage
        """
        pass

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
        默认调用 _cleanup() 方法（该方法会调用 _cleanup_internal()）。

        子类可以重写 _cleanup() 或 _cleanup_internal() 方法来实现自定义清理逻辑。
        """
        await self._cleanup()

    async def _setup_internal(self):  # noqa: B027
        """
        内部初始化方法(子类可选重写)

        子类可以重写此方法来执行初始化逻辑,
        如建立连接、打开文件句柄等。
        此方法在start()开始时调用。
        """
        pass

    async def _cleanup_internal(self):  # noqa: B027
        """
        内部清理方法(子类可选重写)

        子类可以重写此方法来执行清理逻辑,
        如关闭连接、释放文件句柄等。
        此方法在stop()时调用。
        """
        pass

    async def _cleanup(self):  # noqa: B027
        """
        清理资源别名方法(向后兼容)

        此方法调用 _cleanup_internal() 以保持向后兼容性。
        子类应该重写 _cleanup_internal() 而非此方法。
        """
        await self._cleanup_internal()
