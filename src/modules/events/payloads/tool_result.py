"""
事件 Payload 定义：tool.result.* 异步工具结果

定义 ``ToolResultPayload`` 类（不绑定具体 ``tool.result.<name>`` 事件名）。

契约约定：
- emit 时使用具体名（``tool.result.speak`` / ``tool.result.summarize_timeline``）
- 订阅者可一站式监听 ``tool.result.#`` 通配，按 ``tool_name`` 字段分发
- 工具执行层在完成后写对应存储（speak→live_chat、summarize→timeline_summary）；
  本 Payload **不直接落存储**
"""

from typing import Any, Dict, Literal, Optional

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.time_utils import now_ms


# 注意：ToolResultPayload **不注册**到具体 ``tool.result.*`` 事件名
# （避免给反射 / ``list_registered_events()`` 返回空名）。
# 异步工具执行层 emit 具体名 ``tool.result.<tool_name>`` 时使用本类作为 payload 类型；
# 订阅者通过 ``event_bus.on("tool.result.#", ...)`` 通配监听后，按 ``tool_name`` 字段分发。
class ToolResultPayload(BasePayload):
    """
    异步工具结果事件 Payload（fire-and-forget → 结果回传通道）

    事件名：emit 时使用具体名 ``tool.result.<tool_name>``（如 ``tool.result.speak``）；
    订阅者通常使用通配模式 ``event_bus.on("tool.result.#", ...)`` 一站式监听，
    handler 内按 ``tool_name`` 字段分发到对应工具回调。

    发布者：异步工具执行层（fire-and-forget 工具完成后）
    订阅者：Planner（订 ``tool.result.#``，handler 按 ``tool_name`` 分发）

    Attributes:
        tool_name: 工具名（与 emit 时使用的具体事件名后缀一致，如 "speak"/"summarize_timeline"）
        live_session_id: 场次主键（live_sessions.id）。发布方不填，由事件总线的
            场次盖章拦截器统一注入当前进行中场次；0 表示未归属。
        round_id: 关联的决策轮次 ID（可选）。工具调用发生在某轮决策上下文内时
            由调用方填写，供观察器把工具结果与决策轮成组。
        caller_source: 调用方标识（可选）。透传自 ToolInvocation.source
            （"planner-react"=主播决策循环 / "minecraft-react"=游戏 Agent 循环），
            供观察器区分工具调用的归属 Agent。
        status: 执行状态（success=成功完成 / error=执行失败）
        arguments: 透传自调用方的入参（ToolInvocation.arguments 的浅拷贝；
            WebUI 等观察器据此把工具结果与入参对齐展示）
        result: 工具执行结果数据（结构由各工具自行定义）
        error_message: 错误信息（status=error 时填写）
        timestamp_ms: 完成时刻（Unix 毫秒）
    """

    tool_name: str = Field(..., description="工具名（与具体事件名后缀一致，如 'speak'/'summarize_timeline'）")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）。发布方不填，由场次盖章拦截器注入当前进行中场次；0 表示未归属。",
    )
    round_id: Optional[str] = Field(
        default=None,
        description="关联的决策轮次 ID；工具调用不在决策轮上下文内时为 None",
    )
    caller_source: Optional[str] = Field(
        default=None,
        description="调用方标识（透传自 ToolInvocation.source；区分归属 Agent）",
    )
    status: Literal["success", "error"] = Field(..., description="执行状态（success/error）")
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="调用方入参（透传自 ToolInvocation.arguments 的浅拷贝，供 WebUI 等观察器对齐展示；缺省/None 时落空 dict）",
    )
    result: Dict[str, Any] = Field(
        default_factory=dict,
        description="工具执行结果数据（结构由各工具自行定义）",
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
