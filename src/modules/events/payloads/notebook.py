"""
事件 Payload 定义：streamer.notebook.changed

定义 ``streamer.notebook.changed`` 主播备忘录观测事件 Payload。

主播备忘录（``StreamerNotebook``，经 ``streamer_notebook`` 工具写入的本场
工作笔记）发生写入或清空时发一条，``notebook`` 携带变更后的备忘录全文快照。
事件经 ``EventHistoryRecorder`` 落盘，复盘据此还原"主播任一时刻相信什么"。

字段约束：
- ``live_session_id``：场次主键（live_sessions.id）。发布方不填，由事件总线的
  场次盖章拦截器统一注入当前进行中场次；0 表示未归属——与 ``streamer.speech``
  同路，按场回看归组依赖该字段。
- ``notebook``：变更后的备忘录全文（覆盖写语义下的最新全文；清空为空串）。
"""

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event


@register_event(CoreEvents.STREAMER_NOTEBOOK_CHANGED)
class StreamerNotebookChangedPayload(BasePayload):
    """
    主播备忘录观测事件 Payload

    事件名：``streamer.notebook.changed`` —— 主播备忘录写入或清空。
    终点广播：观察面渲染用，消费者不得触发新决策（防环约束）。

    Attributes:
        notebook: 变更后的备忘录全文（全量覆盖写语义下的最新全文；清空为空串）。
        live_session_id: 场次主键（live_sessions.id）；发布方不填，由场次盖章
            拦截器注入当前进行中场次；0 表示未归属。
    """

    notebook: str = Field(..., description="变更后的备忘录全文（清空为空串）")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )


__all__ = [
    "StreamerNotebookChangedPayload",
]
