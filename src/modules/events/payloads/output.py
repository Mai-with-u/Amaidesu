"""
Output 阶段 事件 Payload 定义

定义 Output 阶段 相关的事件 Payload 类型。
- OBSCommandPayload: 统一的 OBS 命令事件 Payload
  整合了发送文本、切换场景、设置源可见性、远程流请求图像等命令，
  通过 ``action`` 字段区分。
- OutputIntentDispatchedPayload: Intent 分发事件 Payload

事件名：
- CoreEvents.OUTPUT_OBS_COMMAND = "output.obs.command"
- CoreEvents.OUTPUT_INTENT_DISPATCHED = "output.intent.dispatched"
"""

from typing import Literal, Optional

from pydantic import ConfigDict

from src.modules.events.payloads.base import BasePayload
from src.modules.events.payloads.decision import IntentPayload
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


@register_event("output.intent.dispatched")
class OutputIntentDispatchedPayload(IntentPayload):
    """Intent 分发事件 Payload

    OutputHandlerManager 在分发给 OutputHandlers 时发布此事件。

    事件名：CoreEvents.OUTPUT_INTENT_DISPATCHED
    发布者：OutputHandlerManager（Output 阶段）
    订阅者：所有 OutputHandler

    继承 IntentPayload 字段（intent_data, name）以便订阅者使用相同的反序列化逻辑。
    通过独立类型标记事件源来自 OutputHandlerManager，便于日志/调试与未来扩展。
    """

    pass


@register_event("output.sticker.command")
class StickerCommandPayload(BasePayload):
    """贴纸触发事件 Payload

    StickerHandler 通过此事件通知其他 Handler（典型为 VTSHandler）显示贴纸，
    并附带渲染参数，使订阅者无需反查配置即可完成渲染。

    事件名：CoreEvents.OUTPUT_STICKER_COMMAND
    发布者：StickerHandler（Output 阶段）
    订阅者：VTSHandler 等需要响应贴纸的 Handler

    Attributes:
        sticker_id: 贴纸标识符（由 StickerHandler 生成，用于去重与冷却控制）
        target_handler: 目标 Handler 名（如 "vts"），由订阅者用于过滤
        timestamp_ms: 触发时间戳（毫秒）
        image_base64: 可选的图片数据（base64 编码的 PNG）。
            当前 Intent 结构不含图片字段，此字段预留供未来扩展。
        size: 贴纸缩放比例（0.0 ~ 1.0）
        rotation: 贴纸旋转角度（0 ~ 360）
        position_x: 贴纸 X 位置（-1.0 ~ 1.0）
        position_y: 贴纸 Y 位置（-1.0 ~ 1.0）
        display_duration_seconds: 显示时长（秒），超时后订阅者应自动卸载
    """

    sticker_id: str
    target_handler: str
    timestamp_ms: int
    image_base64: Optional[str] = None
    size: Optional[float] = None
    rotation: Optional[int] = None
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    display_duration_seconds: Optional[float] = None


__all__ = [
    "OBSCommandPayload",
    "OutputIntentDispatchedPayload",
    "StickerCommandPayload",
]  # noqa: F401
