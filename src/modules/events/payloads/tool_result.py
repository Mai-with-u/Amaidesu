"""
v2 语义域事件 Payload 定义：tool.result.* 异步工具结果

定义 ``ToolResultPayload`` 类（不绑定具体 ``tool.result.<name>`` 事件名）。
对应 §1.5 异步工具结果回传通道。

按契约（.omo/drafts/amaidesu-v2-event-contract.md "tool.result.#" 节）：
- emit 时使用具体名（``tool.result.speak`` / ``tool.result.summarize_timeline``）
- 订阅者可一站式监听 ``tool.result.#`` 通配，按 ``tool_name`` 字段分发
- 工具执行层在完成后写对应存储（speak→live_chat、summarize→timeline_summary）；
  本 Payload **不直接落存储**
"""

from typing import Any, Dict, Literal

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.time_utils import now_ms


# 注意：ToolResultPayload **不注册**到具体 ``tool.result.*`` 事件名
# （契约：tools 尚未实现，避免给反射 / ``list_registered_events()`` 返回空名）。
# 异步工具执行层 emit 具体名 ``tool.result.<tool_name>`` 时使用本类作为 payload 类型；
# 订阅者通过 ``event_bus.on("tool.result.#", ...)`` 通配监听后，按 ``tool_name`` 字段分发。
class ToolResultPayload(BasePayload):
    """
    异步工具结果事件 Payload（fire-and-forget → 结果回传通道）

    事件名：emit 时使用具体名 ``tool.result.<tool_name>``（如 ``tool.result.speak``）；
    订阅者通常使用通配模式 ``event_bus.on("tool.result.#", ...)`` 一站式监听，
    handler 内按 ``tool_name`` 字段分发到对应工具回调。

    发布者：异步工具执行层（fire-and-forget 工具完成后）
    订阅者：Planner（订 ``tool.result.#``，handler 按 ``tool_name`` 分发 §1.46）

    Attributes:
        tool_name: 工具名（与 emit 时使用的具体事件名后缀一致，如 "speak"/"summarize_timeline"）
        status: 执行状态（success=成功完成 / error=执行失败）
        result: 工具执行结果数据（结构由各工具自行定义）；
            **W3 的 ToolExecutionResult 落地后收紧类型**（届时替换 ``Dict[str, Any]`` 为具体类型）
        error_message: 错误信息（status=error 时填写）
        timestamp_ms: 完成时刻（Unix 毫秒）
    """

    tool_name: str = Field(..., description="工具名（与具体事件名后缀一致，如 'speak'/'summarize_timeline'）")
    status: Literal["success", "error"] = Field(..., description="执行状态（success/error）")
    # W3 的 ToolExecutionResult 落地后收紧类型
    result: Dict[str, Any] = Field(
        default_factory=dict,
        description="工具执行结果数据（结构由各工具自行定义）；W3 的 ToolExecutionResult 落地后收紧类型",
    )
    error_message: str = Field(default="", description="错误信息（status=error 时填写）")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="完成时刻（Unix 毫秒）",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tool_name": "speak",
                "status": "success",
                "result": {"speech_text": "你好！很高兴见到你~", "audio_duration_ms": 3200},
                "error_message": "",
                "timestamp_ms": 1706745600000,
            }
        }
    )


__all__ = ["ToolResultPayload"]
