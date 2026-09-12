"""
事件 Payload 模块（按语义域组织）

为 EventBus 事件提供类型安全的 Pydantic Payload 定义，每个 Payload 经
``@register_event`` 绑定到具体事件名（tool.result.* 为通配族，不绑定具体名）。
RoomMessagePayload 保留 ConnectedPayload / DisconnectedPayload 同构字段，
供旧连接/断开语义的兼容导入。

模块结构：
- core.py: Core 系统事件 Payload（core.startup/shutdown/error）
- live.py: 场次生命周期（live.started/live.ended，LiveSessionManager 发布）
- room.py: 直播间行为流（room.message.*）
- perception.py: 主播感知流（perception.screen）
- game.py: 游戏里程碑（game.*）
- rundown.py: 流程单变更（rundown.changed）
- planner.py: 决策轮记录 / 阶段状态（planner.decision / streamer.stage）
- tool_result.py: 异步工具结果（tool.result.*，不绑定具体名）
- utterance.py: TTS 一次发声实例生命周期（tts.utterance.*）
- speech.py: 主播发言业务事实（streamer.speech）

使用示例:
    from src.modules.events.payloads import RoomMessagePayload
    from src.modules.events.names import CoreEvents

    # 发送事件
    await event_bus.emit(
        CoreEvents.ROOM_MESSAGE_DANMAKU,
        RoomMessagePayload(...),
        source="bilibili_danmaku",
    )

    # 订阅事件（类型提示）
    @event_bus.on(CoreEvents.ROOM_MESSAGE_DANMAKU)
    async def handle_danmaku(payload: RoomMessagePayload):
        logger.debug(f"收到弹幕: {payload.content}")
"""

from src.modules.logging import get_logger

from .core import (
    CoreErrorPayload,
    CoreShutdownPayload,
    CoreStartupPayload,
)
from .game import GamePayload
from .live import (
    LiveEndedPayload,
    LiveStartedPayload,
)
from .planner import (
    PlannerBatchItem,
    PlannerDecisionPayload,
    PlannerVerdictPayload,
    StreamerStagePayload,
)
from .room import (
    GiftInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)
from .rundown import RundownChangedPayload
from .speech import StreamerSpeechPayload
from .tasks import TaskChangedPayload
from .tool_health import ToolHealthPayload
from .tool_result import ToolResultPayload
from .utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)

logger = get_logger("Payloads")

__all__ = [
    "ScreenDescriptionPayload",
    # Core 系统事件
    "CoreStartupPayload",
    "CoreShutdownPayload",
    "CoreErrorPayload",
    # live 语义域 — 场次生命周期
    "LiveStartedPayload",
    "LiveEndedPayload",
    # room.message.* 直播间行为流
    "RoomMessageUser",
    "GiftInfo",
    "SuperChatInfo",
    "RoomMessagePayload",
    # game.* 游戏里程碑
    "GamePayload",
    # planner / streamer 决策管线
    "PlannerBatchItem",
    "PlannerDecisionPayload",
    "PlannerVerdictPayload",
    "StreamerStagePayload",
    # rundown 流程单子系统
    "RundownChangedPayload",
    # task.* 异步任务生命周期
    "TaskChangedPayload",
    # tool.result.* 异步工具结果
    "ToolResultPayload",
    # tool.health.* 工具健康状态变更
    "ToolHealthPayload",
    # tts.utterance.* 发声实例生命周期
    "UtteranceStartedPayload",
    "UtteranceFinishedPayload",
    "UtteranceFailedPayload",
    # streamer.speech 主播发言业务事实
    "StreamerSpeechPayload",
]
