"""
Output 阶段 事件 Payload 定义

定义 Output 阶段 相关的事件 Payload 类型。
- OBSCommandPayload: 统一的 OBS 命令事件 Payload
  整合了发送文本、切换场景、设置源可见性、远程流请求图像等命令，
  通过 ``action`` 字段区分。

事件名：CoreEvents.OUTPUT_OBS_COMMAND = "output.obs.command"
"""

from typing import Literal, Optional

from pydantic import ConfigDict

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event


@register_event("output.obs.command")
class OBSCommandPayload(BasePayload):
    """统一的 OBS 命令 Payload

    所有 OBS 相关操作（发送文本、切换场景、设置源可见性、远程流请求图像）
    都通过本 Payload 的 ``action`` 字段区分。

    事件名：CoreEvents.OUTPUT_OBS_COMMAND
    发布者：Dashboard API、外部组件
    订阅者：ObsControlHandler、RemoteStreamHandler 等

    Attributes:
        action: 命令类型，可选值见 Literal 类型定义。
        text: send_text 字段，要发送的文本。
        scene_name: switch_scene 字段，目标场景名称。
        source_name: send_text / set_source_visibility 字段，OBS 源名称。
        visibility: set_source_visibility 字段，是否可见。
        url: request_image 字段，请求图像的 URL。
        timeout_seconds: request_image 字段，请求超时秒数。
        timestamp_ms: request_image 字段，请求时间戳（毫秒）。
    """

    action: Literal["send_text", "switch_scene", "set_source_visibility", "request_image"]

    # send_text 字段
    text: Optional[str] = None

    # switch_scene 字段
    scene_name: Optional[str] = None

    # set_source_visibility 字段
    source_name: Optional[str] = None
    visibility: Optional[bool] = None

    # request_image 字段
    url: Optional[str] = None
    timeout_seconds: Optional[float] = None
    timestamp_ms: Optional[int] = None

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {"action": "send_text", "text": "Hello World", "source_name": "文本"},
                {"action": "switch_scene", "scene_name": "主场景"},
                {"action": "set_source_visibility", "source_name": "摄像头", "visibility": True},
                {
                    "action": "request_image",
                    "url": "http://example.com/img.jpg",
                    "timeout_seconds": 5.0,
                    "timestamp_ms": 1706745600000,
                },
            ]
        },
    )


__all__ = [
    "OBSCommandPayload",
]
