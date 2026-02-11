"""
渲染参数数据类 - 跨域共享类型

定义了从参数生成传递到渲染输出的参数结构。
为了符合3域架构，从 Output Domain 迁移到 Modules 层。
"""

import time
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class ExpressionParameters(BaseModel):
    """
    表达参数 - 参数生成输出给渲染输出的完整参数

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
    expressions: Dict[str, float] = Field(default_factory=dict)
    expressions_enabled: bool = True

    # 热键相关
    hotkeys: List[str] = Field(default_factory=list)
    hotkeys_enabled: bool = True

    # 动作相关
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    actions_enabled: bool = True

    # 元数据
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 优先级
    priority: int = 100

    # 时间戳
    timestamp: float = Field(default_factory=time.time)

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


# 向后兼容别名
RenderParameters = ExpressionParameters


__all__ = [
    "ExpressionParameters",
    "RenderParameters",
]
