"""
事件 Payload 定义：streamer 递话到达事实

- ``streamer.prompt_received``：主播 Agent 受理递话成功即发一条（含全文原文）。
  与 ``agent.prompted``（观测面，两条调用面在 AgentControl 收口）互补：本事件
  在递话进入提醒队列的单一受理点发出，兼作续播重放的数据源——进程重启接回
  场次时，从诊断事件流文件回读上一进程窗口的递话补进提醒队列，重启不再吞掉
  运营留言。
"""

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.utils.time_utils import now_ms


@register_event(CoreEvents.STREAMER_PROMPT_RECEIVED)
class StreamerPromptReceivedPayload(BasePayload):
    """
    递话到达主播 Agent 事件 Payload（receive_prompt 受理成功的单一受理点）。

    Attributes:
        content: 递话内容全文（不截断；应用日志只留 60 字符，全文以本事件为准）
        source: 发起方标识（运营 REST / 跨 Agent 调用面各自的自述）
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    content: str = Field(..., description="递话内容全文（不截断）")
    source: str = Field(default="", description="发起方标识")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


__all__ = ["StreamerPromptReceivedPayload"]
