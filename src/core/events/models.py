"""
核心事件数据模型

使用 Pydantic BaseModel 定义，提供：
- 运行时类型验证
- 自动序列化/反序列化
- IDE 类型提示
- 自动文档生成
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict
import time


# ==================== Layer 1: 输入感知 ====================


class RawDataEvent(BaseModel):
    """
    原始数据事件

    事件名：perception.raw_data.generated
    发布者：InputProvider
    订阅者：InputLayer（Layer 2）
    """

    content: Any = Field(..., description="原始数据内容（bytes, str, dict等）")
    source: str = Field(..., min_length=1, description="数据源标识符")
    data_type: str = Field(..., description="数据类型")
    timestamp: float = Field(default_factory=time.time, description="Unix时间戳（秒）")
    preserve_original: bool = Field(default=False, description="是否保留原始数据")
    original_data: Optional[Any] = Field(default=None, description="原始数据（如果已处理）")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "用户输入的文本",
                "source": "console_input",
                "data_type": "text",
                "timestamp": 1706745600.0,
                "metadata": {"user_id": "12345"},
            }
        }
    )


# ==================== Layer 2: 输入标准化 ====================


class NormalizedTextEvent(BaseModel):
    """
    标准化文本事件

    事件名：normalization.text.ready
    发布者：InputLayer
    订阅者：CanonicalMessageBuilder（Layer 3）
    """

    text: str = Field(..., min_length=1, description="标准化后的文本")
    source: str = Field(..., min_length=1, description="数据源")
    data_type: str = Field(default="text", description="数据类型")
    timestamp: float = Field(default_factory=time.time, description="时间戳")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "text": "你好，今天天气怎么样？",
                "source": "bili_danmaku",
                "data_type": "text",
                "timestamp": 1706745600.0,
                "metadata": {"user": "观众A", "room_id": "123456"},
            }
        }
    )


# ==================== Layer 4: 决策层 ====================


class DecisionRequestEvent(BaseModel):
    """
    决策请求事件

    事件名：decision.request
    发布者：CanonicalMessageBuilder
    订阅者：DecisionManager
    """

    canonical_message: Dict[str, Any] = Field(..., description="规范化消息")
    context: Dict[str, Any] = Field(default_factory=dict, description="上下文信息")
    priority: int = Field(default=100, ge=0, le=1000, description="优先级")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "canonical_message": {
                    "text": "你好",
                    "sender": {"id": "user_123", "name": "观众A"},
                },
                "context": {"conversation_id": "conv_456"},
                "priority": 100,
            }
        }
    )


class DecisionResponseEvent(BaseModel):
    """
    决策响应事件

    事件名：decision.response_generated
    发布者：DecisionProvider
    订阅者：UnderstandingLayer（Layer 5）
    """

    response: Dict[str, Any] = Field(..., description="决策响应（MessageBase格式）")
    provider: str = Field(..., description="决策Provider名称")
    latency_ms: float = Field(default=0, ge=0, description="决策延迟（毫秒）")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


# ==================== Layer 5: 表现理解 ====================


class IntentGeneratedEvent(BaseModel):
    """
    意图生成事件

    事件名：understanding.intent_generated
    发布者：UnderstandingLayer
    订阅者：ExpressionLayer（Layer 6）
    """

    original_text: str = Field(..., description="原始文本")
    emotion: str = Field(..., description="情感类型")
    response_text: str = Field(..., description="响应文本")
    actions: List[Dict[str, Any]] = Field(default_factory=list, description="动作列表")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    timestamp: float = Field(default_factory=time.time, description="时间戳")


# ==================== Layer 6: 表现生成 ====================


class ExpressionParametersEvent(BaseModel):
    """
    表现参数事件

    事件名：expression.parameters_generated
    发布者：ExpressionLayer
    订阅者：OutputProvider（Layer 7）
    """

    tts_text: str = Field(default="", description="TTS 文本")
    tts_enabled: bool = Field(default=True, description="是否启用 TTS")
    subtitle_text: str = Field(default="", description="字幕文本")
    subtitle_enabled: bool = Field(default=True, description="是否启用字幕")
    expressions: Dict[str, float] = Field(default_factory=dict, description="表情参数")
    expressions_enabled: bool = Field(default=True, description="是否启用表情")
    hotkeys: List[str] = Field(default_factory=list, description="热键列表")
    hotkeys_enabled: bool = Field(default=True, description="是否启用热键")
    actions: List[Dict[str, Any]] = Field(default_factory=list, description="动作列表")
    actions_enabled: bool = Field(default=True, description="是否启用动作")
    priority: int = Field(default=100, ge=0, description="优先级")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    timestamp: float = Field(default_factory=time.time, description="时间戳")

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
                "priority": 100,
            }
        }
    )


# ==================== 系统事件 ====================


class SystemErrorEvent(BaseModel):
    """
    系统错误事件

    事件名：core.error
    发布者：任何组件
    订阅者：错误处理器
    """

    error_type: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误消息")
    source: str = Field(..., description="错误源")
    stack_trace: Optional[str] = Field(default=None, description="堆栈跟踪")
    context: Dict[str, Any] = Field(default_factory=dict, description="上下文信息")
    timestamp: float = Field(default_factory=time.time, description="时间戳")
    recoverable: bool = Field(default=True, description="是否可恢复")
