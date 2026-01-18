"""
渲染参数数据类 - Layer 5到Layer 6的数据格式

定义了从Expression生成层传递到Rendering呈现层的参数结构。
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
import time


@dataclass
class ExpressionParameters:
    """
    表达参数 - Layer 5输出给Layer 6的完整参数

    Attributes:
        tts_text: TTS文本内容
        subtitle_text: 字幕文本内容
        expressions: VTS表情参数字典
        hotkeys: 热键列表
        actions: 动作列表
        metadata: 扩展元数据
        priority: 优先级（数字越小越优先）
        timestamp: 时间戳
    """

    # TTS相关
    tts_text: str = ""
    tts_enabled: bool = True

    # 字幕相关
    subtitle_text: str = ""
    subtitle_enabled: bool = True

    # VTS表情相关
    expressions: Dict[str, float] = field(default_factory=dict)
    expressions_enabled: bool = True

    # 热键相关
    hotkeys: List[str] = field(default_factory=list)
    hotkeys_enabled: bool = True

    # 动作相关
    actions: List[Dict[str, Any]] = field(default_factory=list)
    actions_enabled: bool = True

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    # 优先级
    priority: int = 100

    # 时间戳
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        """初始化后处理，确保metadata和expressions为空字典而非None"""
        if self.metadata is None:
            self.metadata = {}
        if self.expressions is None:
            self.expressions = {}

    def to_dict(self) -> dict:
        """
        序列化为字典

        Returns:
            序列化后的字典
        """
        return {
            "tts_text": self.tts_text,
            "tts_enabled": self.tts_enabled,
            "subtitle_text": self.subtitle_text,
            "subtitle_enabled": self.subtitle_enabled,
            "expressions": self.expressions,
            "expressions_enabled": self.expressions_enabled,
            "hotkeys": self.hotkeys,
            "hotkeys_enabled": self.hotkeys_enabled,
            "actions": self.actions,
            "actions_enabled": self.actions_enabled,
            "metadata": self.metadata,
            "priority": self.priority,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExpressionParameters":
        """
        从字典反序列化

        Args:
            data: 字典数据

        Returns:
            ExpressionParameters实例
        """
        return cls(
            tts_text=data.get("tts_text", ""),
            tts_enabled=data.get("tts_enabled", True),
            subtitle_text=data.get("subtitle_text", ""),
            subtitle_enabled=data.get("subtitle_enabled", True),
            expressions=data.get("expressions", {}),
            expressions_enabled=data.get("expressions_enabled", True),
            hotkeys=data.get("hotkeys", []),
            hotkeys_enabled=data.get("hotkeys_enabled", True),
            actions=data.get("actions", []),
            actions_enabled=data.get("actions_enabled", True),
            metadata=data.get("metadata", {}),
            priority=data.get("priority", 100),
            timestamp=data.get("timestamp", time.time()),
        )

    def __repr__(self) -> str:
        """字符串表示"""
        return (
            f"ExpressionParameters("
            f"tts={len(self.tts_text)}, "
            f"subtitle={len(self.subtitle_text)}, "
            f"expressions={len(self.expressions)}, "
            f"hotkeys={len(self.hotkeys)}, "
            f"actions={len(self.actions)})"
        )


# 向后兼容：保持RenderParameters别名
RenderParameters = ExpressionParameters
