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
    from src.modules.streaming.audio_stream_channel import AudioStreamChannel
    from src.modules.types import RenderParameters


class OutputProvider(ABC):
    """
    输出Provider抽象基类

    职责: 将渲染参数输出到目标设备

    生命周期:
    1. 实例化(__init__)
    2. 启动(start()) - 注册到EventBus
    3. 渲染(render()) - 执行渲染
    4. 停止(stop()) - 释放资源

    Attributes:
        config: Provider配置(来自rendering.outputs.xxx配置)
        event_bus: EventBus实例(可选,用于事件通信)
        is_started: 是否已启动
        audio_stream_channel: AudioStreamChannel实例(可选,用于音频流传输)
    """

    def __init__(self, config: dict):
        """
        初始化Provider

        Args:
            config: Provider配置(来自rendering.outputs.xxx配置)
        """
        self.config = config
        self.event_bus = None
        self.is_started = False
        self._audio_stream_channel: Optional["AudioStreamChannel"] = None

    @property
    def audio_stream_channel(self) -> Optional["AudioStreamChannel"]:
        """获取 AudioStreamChannel 实例"""
        return self._audio_stream_channel

    async def start(self, event_bus, audio_stream_channel: Optional["AudioStreamChannel"] = None):
        """
        启动Provider

        Args:
            event_bus: EventBus实例
            audio_stream_channel: 可选的AudioStreamChannel实例（用于音频流传输）

        Raises:
            ConnectionError: 如果无法连接到目标设备
        """
        self.event_bus = event_bus
        self._audio_stream_channel = audio_stream_channel
        try:
            await self._start_internal()
            self.is_started = True
        except Exception:
            # 如果启动失败，保持当前状态不变
            # 由调用者决定如何处理错误
            raise

    async def render(self, parameters: "RenderParameters"):
        """
        渲染参数

        Args:
            parameters: 渲染参数

        Raises:
            RuntimeError: 如果Provider未启动
            Exception: 渲染过程中的错误
        """
        if not self.is_started:
            raise RuntimeError("Provider not started, call start() first")
        await self._render_internal(parameters)

    async def _start_internal(self):  # noqa: B027
        """内部启动逻辑(子类可选重写)"""
        # 子类可以重写此方法来执行启动逻辑,如连接到设备、加载资源等。
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

    async def stop(self):
        """
        停止Provider并清理资源

        停止Provider并释放所有资源。
        """
        # 如果已经停止，直接返回
        if not self.is_started:
            return

        try:
            await self._stop_internal()
            self.is_started = False
        except Exception:
            # 如果停止失败，保持当前状态不变
            # 不重新抛出异常，允许错误隔离
            pass

    def get_info(self) -> Dict[str, Any]:
        """
        获取Provider信息

        Returns:
            Provider信息字典，包含:
            - name: Provider名称
            - is_started: 是否已启动
            - type: Provider类型
        """
        return {
            "name": self.__class__.__name__,
            "is_started": self.is_started,
            "type": "output_provider",
        }

    async def _stop_internal(self):  # noqa: B027
        """内部停止逻辑(子类可选重写)"""
        # 子类可以重写此方法来执行停止逻辑,如关闭连接、释放资源等。
        ...
