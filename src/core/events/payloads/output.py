"""
Output Domain 事件 Payload 定义

定义 Output Domain 相关的事件 Payload 类型。
- ParametersGeneratedPayload: 表情参数生成事件
- RenderCompletedPayload: 渲染完成事件
- RenderFailedPayload: 渲染失败事件
"""

from typing import Any, Dict, List, Optional, TYPE_CHECKING
from pydantic import Field, ConfigDict
import time

from src.core.events.payloads.base import BasePayload

if TYPE_CHECKING:
    from src.domains.output.parameters.render_parameters import ExpressionParameters


class ParametersGeneratedPayload(BasePayload):
    """
    表情参数生成事件 Payload

    事件名：CoreEvents.EXPRESSION_PARAMETERS_GENERATED
    发布者：ExpressionGenerator (Output Domain: 参数生成)
    订阅者：OutputProvider (Output Domain: 渲染输出)

    表示 ExpressionGenerator 已根据 Intent 生成了渲染参数。
    OutputProvider 订阅此事件并执行实际的渲染输出。
    """

    # TTS 相关
    tts_text: str = Field(default="", description="TTS 文本内容")
    tts_enabled: bool = Field(default=True, description="是否启用 TTS")

    # 字幕相关
    subtitle_text: str = Field(default="", description="字幕文本内容")
    subtitle_enabled: bool = Field(default=True, description="是否启用字幕")

    # VTS 表情相关
    expressions: Dict[str, float] = Field(default_factory=dict, description="VTS 表情参数字典")
    expressions_enabled: bool = Field(default=True, description="是否启用表情")

    # 热键相关
    hotkeys: List[str] = Field(default_factory=list, description="热键列表")
    hotkeys_enabled: bool = Field(default=True, description="是否启用热键")

    # 动作相关
    actions: List[Dict[str, Any]] = Field(default_factory=list, description="动作列表")
    actions_enabled: bool = Field(default=True, description="是否启用动作")

    # 元数据
    metadata: Dict[str, Any] = Field(default_factory=dict, description="扩展元数据")
    priority: int = Field(default=100, ge=0, description="优先级（数字越小越优先）")
    timestamp: float = Field(default_factory=time.time, description="时间戳")

    # 来源信息
    source_intent: Optional[Dict[str, Any]] = Field(default=None, description="来源 Intent（用于追踪）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tts_text": "你好呀~",
                "tts_enabled": True,
                "subtitle_text": "你好呀~",
                "subtitle_enabled": True,
                "expressions": {"happy": 0.8, "surprised": 0.2},
                "expressions_enabled": True,
                "hotkeys": ["wave"],
                "hotkeys_enabled": True,
                "actions": [{"type": "blink", "params": {"count": 2}}],
                "actions_enabled": True,
                "priority": 100,
                "timestamp": 1706745600.0,
            }
        }
    )

    def _debug_fields(self) -> List[str]:
        """返回需要显示的字段"""
        return [
            "tts_text", "tts_enabled",
            "subtitle_text", "subtitle_enabled",
            "expressions", "expressions_enabled",
            "hotkeys", "hotkeys_enabled",
        ]

    @classmethod
    def from_parameters(cls, parameters: "ExpressionParameters", source_intent: Optional[Dict[str, Any]] = None) -> "ParametersGeneratedPayload":
        """
        从 ExpressionParameters 对象创建 Payload

        Args:
            parameters: ExpressionParameters 对象
            source_intent: 来源 Intent（可选）

        Returns:
            ParametersGeneratedPayload 实例
        """
        return cls(
            tts_text=parameters.tts_text,
            tts_enabled=parameters.tts_enabled,
            subtitle_text=parameters.subtitle_text,
            subtitle_enabled=parameters.subtitle_enabled,
            expressions=parameters.expressions.copy(),
            expressions_enabled=parameters.expressions_enabled,
            hotkeys=parameters.hotkeys.copy(),
            hotkeys_enabled=parameters.hotkeys_enabled,
            actions=parameters.actions.copy(),
            actions_enabled=parameters.actions_enabled,
            metadata=parameters.metadata.copy(),
            priority=parameters.priority,
            timestamp=parameters.timestamp,
            source_intent=source_intent,
        )


class RenderCompletedPayload(BasePayload):
    """
    渲染完成事件 Payload

    事件名：CoreEvents.RENDER_COMPLETED
    发布者：OutputProvider (Output Domain)
    订阅者：任何需要监控渲染状态的组件

    表示 OutputProvider 已成功完成渲染输出。
    """

    provider: str = Field(..., description="OutputProvider 名称（如 'tts', 'subtitle', 'vts'）")
    output_type: str = Field(..., description="输出类型（如 'audio', 'text', 'expression'）")
    success: bool = Field(default=True, description="是否成功")
    duration_ms: float = Field(default=0, ge=0, description="渲染耗时（毫秒）")
    timestamp: float = Field(default_factory=time.time, description="完成时间")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider": "tts",
                "output_type": "audio",
                "success": True,
                "duration_ms": 500.0,
                "timestamp": 1706745600.0,
                "metadata": {"text_length": 10, "voice": "default"},
            }
        }
    )

    def _debug_fields(self) -> List[str]:
        """返回需要显示的字段"""
        return ["provider", "output_type", "success", "duration_ms"]


class RenderFailedPayload(BasePayload):
    """
    渲染失败事件 Payload

    事件名：CoreEvents.RENDER_FAILED
    发布者：OutputProvider (Output Domain)
    订阅者：错误处理器、监控组件

    表示 OutputProvider 渲染过程中发生错误。
    """

    provider: str = Field(..., description="OutputProvider 名称")
    output_type: str = Field(..., description="输出类型")
    error_type: str = Field(..., description="错误类型")
    error_message: str = Field(..., description="错误消息")
    timestamp: float = Field(default_factory=time.time, description="失败时间")
    recoverable: bool = Field(default=True, description="是否可恢复")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider": "tts",
                "output_type": "audio",
                "error_type": "ConnectionError",
                "error_message": "无法连接到 TTS 服务",
                "timestamp": 1706745600.0,
                "recoverable": True,
                "metadata": {"retry_count": 1},
            }
        }
    )

    def _debug_fields(self) -> List[str]:
        """返回需要显示的字段"""
        return ["provider", "output_type", "error_type", "error_message", "recoverable"]
