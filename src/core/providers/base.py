"""
Provider接口基础数据类定义

定义了新架构中的核心数据结构:
- RenderParameters: 渲染参数(传递给Layer 6: 渲染呈现层)

注意:
- RawData 和 NormalizedText 已移动到 src/core/data_types/
- CanonicalMessage 已移动到 src/canonical/
"""

from dataclasses import dataclass, field

# 从 canonical 导入 CanonicalMessage
from src.canonical.canonical_message import CanonicalMessage

__all__ = ["RenderParameters", "CanonicalMessage"]


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
