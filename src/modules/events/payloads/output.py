"""
Output Domain 事件 Payload 定义

定义 Output Domain 相关的事件 Payload 类型。
- RenderCompletedPayload: 渲染完成事件
- RenderFailedPayload: 渲染失败事件
"""

import time
from typing import Any, Dict

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload


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

    def __str__(self) -> str:
        """简化格式：显示渲染完成信息"""
        class_name = self.__class__.__name__
        return f'{class_name}(provider="{self.provider}", output_type="{self.output_type}", success={self.success}, duration_ms={self.duration_ms:.0f})'


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

    def __str__(self) -> str:
        """简化格式：显示渲染失败信息"""
        class_name = self.__class__.__name__
        error_msg = self.error_message[:30] + "..." if len(self.error_message) > 30 else self.error_message
        return f'{class_name}(provider="{self.provider}", output_type="{self.output_type}", error_type="{self.error_type}", error_message="{error_msg}", recoverable={self.recoverable})'


class OBSSendTextPayload(BasePayload):
    """
    OBS 发送文本事件 Payload

    事件名：CoreEvents.OBS_SEND_TEXT（待定义）
    发布者：OBS 控制相关组件
    订阅者：OBSOutputProvider

    用于向 OBS 文本源发送文本内容。
    """

    text: str = Field(..., description="要发送的文本")
    source_name: str = Field(default="文本", description="OBS 源名称")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "text": "Hello World",
                "source_name": "文本",
            }
        }
    )

    def __str__(self) -> str:
        """简化格式：显示发送文本信息"""
        class_name = self.__class__.__name__
        text_preview = self.text[:20] + "..." if len(self.text) > 20 else self.text
        return f'{class_name}(source_name="{self.source_name}", text="{text_preview}")'


class OBSSwitchScenePayload(BasePayload):
    """
    OBS 切换场景事件 Payload

    事件名：CoreEvents.OBS_SWITCH_SCENE（待定义）
    发布者：场景切换相关组件
    订阅者：OBSOutputProvider

    用于切换 OBS 中的场景。
    """

    scene_name: str = Field(..., description="目标场景名称")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "scene_name": "主场景",
            }
        }
    )

    def __str__(self) -> str:
        """简化格式：显示场景切换信息"""
        class_name = self.__class__.__name__
        return f'{class_name}(scene_name="{self.scene_name}")'


class OBSSetSourceVisibilityPayload(BasePayload):
    """
    OBS 设置源可见性事件 Payload

    事件名：CoreEvents.OBS_SET_SOURCE_VISIBILITY（待定义）
    发布者：源可见性控制相关组件
    订阅者：OBSOutputProvider

    用于设置 OBS 中源的可见性。
    """

    source_name: str = Field(..., description="OBS 源名称")
    visible: bool = Field(..., description="是否可见")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_name": "摄像头",
                "visible": True,
            }
        }
    )

    def __str__(self) -> str:
        """简化格式：显示源可见性信息"""
        class_name = self.__class__.__name__
        return f'{class_name}(source_name="{self.source_name}", visible={self.visible})'


class RemoteStreamRequestImagePayload(BasePayload):
    """
    远程流请求图像事件 Payload

    事件名：CoreEvents.REMOTE_STREAM_REQUEST_IMAGE
    发布者：RemoteStreamOutputProvider
    订阅者：需要响应图像请求的组件（如 AvatarOutputProvider）

    当边缘设备向服务器请求一帧图像时触发。
    """

    timestamp: float = Field(default_factory=time.time, description="请求时间戳")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "timestamp": 1706745600.0,
            }
        }
    )

    def __str__(self) -> str:
        """简化格式：显示图像请求信息"""
        class_name = self.__class__.__name__
        return f"{class_name}(timestamp={self.timestamp})"


__all__ = [
    "RenderCompletedPayload",
    "RenderFailedPayload",
    "OBSSendTextPayload",
    "OBSSwitchScenePayload",
    "OBSSetSourceVisibilityPayload",
    "RemoteStreamRequestImagePayload",
]
