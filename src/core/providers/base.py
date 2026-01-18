"""
Provider接口基础数据类定义

定义了新架构中的核心数据结构:
- RawData: 原始数据(来自Layer 1: 输入感知层)
- RenderParameters: 渲染参数(传递给Layer 6: 渲染呈现层)
- CanonicalMessage: 标准消息(Layer 3: 中间表示层)
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RawData:
    """
    原始数据

    来自输入感知层(Layer 1)的原始数据，可以包括:
    - 弹幕文本
    - 游戏事件数据
    - 语音音频数据
    - 屏幕截图数据
    等任意格式

    Attributes:
        content: 原始内容(bytes, str, dict等)
        source: 数据源标识(如"bili_danmaku", "minecraft", "console")
        data_type: 数据类型(如"text", "audio", "image", "json", "event")
        timestamp: 时间戳(Unix时间戳,秒)
        preserve_original: 是否保留原始数据到缓存
        metadata: 额外的元数据(用于扩展和特殊用途)
    """

    content: Any
    source: str
    data_type: str
    timestamp: float
    preserve_original: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}


@dataclass
class RenderParameters:
    """
    渲染参数

    传递给渲染呈现层(Layer 6)的参数,用于控制输出渲染:
    - TTS文本和语音参数
    - 字幕文本和显示参数
    - VTS表情和动作参数
    - 图像渲染参数

    Attributes:
        content: 渲染内容(文本、音频数据、图像数据等)
        render_type: 渲染类型(如"text", "audio", "image", "animation", "subtitle")
        metadata: 渲染元数据(用于传递额外的渲染参数)
        priority: 优先级(数字越小越优先,默认100)
    """

    content: str
    render_type: str
    metadata: dict = field(default_factory=dict)
    priority: int = 100

    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}


@dataclass
class CanonicalMessage:
    """
    标准消息(中间表示)

    Layer 3: 中间表示层的核心数据结构，用于在决策层
    和各层之间传递统一的、标准化的消息格式。

    Attributes:
        text: 标准化后的文本内容
        intent: 意图(可选,用于表达理解层)
        metadata: 元数据(用于传递额外的上下文信息)
    """

    text: str
    intent: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}
