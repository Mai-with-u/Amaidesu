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

    数据流: start() → yield NormalizedMessage

    生命周期:
    1. 实例化(__init__)
    2. 启动(start()) - 返回异步生成器,产生 NormalizedMessage
       - start() 会先调用 init() 进行初始化
       - 然后返回 AsyncIterator
    3. 停止(stop()) - 调用 cleanup() 清理资源

    公开方法:
    - init(): 初始化资源配置（子类可重写）
    - start(): 启动 Provider 并返回 AsyncIterator
    - stop(): 停止 Provider
    - cleanup(): 清理资源（子类可重写）
    - generate(): 生成 NormalizedMessage 数据流（子类必须实现）

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

    async def init(self) -> None:  # noqa: B027
        """
        初始化资源配置（子类可重写）

        执行初始化逻辑，如建立连接、加载配置等。
        此方法在 start() 开始时调用。

        子类可以重写此方法来执行初始化逻辑，
        如建立连接、打开文件句柄等。
        """
        pass

    async def start(self) -> AsyncIterator[NormalizedMessage]:
        """
        启动 Provider 并返回 NormalizedMessage 流

        此方法会先调用 init() 进行初始化，
        然后返回一个 AsyncIterator。

        Yields:
            NormalizedMessage: 标准化消息

        Raises:
            ConnectionError: 如果无法连接到数据源
        """
        await self.init()
        self.is_running = True
        async for message in self.generate():
            yield message

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

    # 向后兼容别名
    async def _setup_internal(self) -> None:  # noqa: B027
        """
        内部初始化方法（向后兼容别名）

        此方法已弃用，请使用 init() 方法代替。
        为保持向后兼容，此方法仍可使用。
        """
        await self.init()

    async def _cleanup_internal(self) -> None:  # noqa: B027
        """
        内部清理方法（向后兼容别名）

        此方法已弃用，请使用 cleanup() 方法代替。
        为保持向后兼容，此方法仍可使用。
        """
        await self.cleanup()
