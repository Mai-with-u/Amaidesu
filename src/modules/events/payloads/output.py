"""
Output Domain 事件 Payload 定义

定义 Output Domain 相关的事件 Payload 类型。
- OBSSendTextPayload: OBS 发送文本事件
- OBSSwitchScenePayload: OBS 切换场景事件
- OBSSetSourceVisibilityPayload: OBS 设置源可见性事件
- RemoteStreamRequestImagePayload: 远程流请求图像事件
"""

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload


class OBSSendTextPayload(BasePayload):
    """
    OBS 发送文本事件 Payload

    事件名：CoreEvents.OUTPUT_OBS_SEND_TEXT
    发布者：需要向 OBS 发送文本的组件
    订阅者：ObsControlOutputProvider

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

    事件名：CoreEvents.OUTPUT_OBS_SWITCH_SCENE
    发布者：需要切换 OBS 场景的组件
    订阅者：ObsControlOutputProvider

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

    事件名：CoreEvents.OUTPUT_OBS_SET_SOURCE_VISIBILITY
    发布者：需要控制源可见性的组件
    订阅者：ObsControlOutputProvider

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

    事件名：CoreEvents.OUTPUT_REMOTE_STREAM_REQUEST_IMAGE
    发布者：RemoteStreamOutputProvider
    订阅者：需要响应图像请求的组件（如 AvatarOutputProvider）

    当边缘设备向服务器请求一帧图像时触发。
    """

    timestamp: float = Field(default_factory=lambda: __import__("time").time(), description="请求时间戳")

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
    "OBSSendTextPayload",
    "OBSSwitchScenePayload",
    "OBSSetSourceVisibilityPayload",
    "RemoteStreamRequestImagePayload",
]
