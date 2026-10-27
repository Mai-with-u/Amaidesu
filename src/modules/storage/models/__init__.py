"""被持久化实体的数据契约子包。

存放存储表对应的数据模型（Pydantic 契约）：仓储据此序列化 / 反序列化，
Agent 与 Dashboard 等消费方依赖方向一律向下（agents → modules、
dashboard → storage），本包不反向依赖任何上层。
"""

from src.modules.storage.models.chat import (
    ChatDateCount,
    DailyDanmakuCount,
    GiftRecord,
    GuardRecord,
    LiveChatMessage,
    SessionSpeaker,
    SessionSpeechMoment,
    SuperChatRecord,
    ViewerSessionParticipation,
)
from src.modules.storage.models.memory import (
    EndedSessionWithoutRecap,
    SessionRecap,
    Viewer,
    ViewerName,
    ViewerProfile,
)
from src.modules.storage.models.rundown import (
    DEFAULT_RUNDOWN,
    Rundown,
    RundownInstance,
    RundownInstanceSegment,
    RundownSegment,
    RundownTransition,
)

__all__ = [
    # chat 域
    "ChatDateCount",
    "DailyDanmakuCount",
    "GiftRecord",
    "GuardRecord",
    "LiveChatMessage",
    "SessionSpeaker",
    "SessionSpeechMoment",
    "SuperChatRecord",
    "ViewerSessionParticipation",
    # memory 域
    "EndedSessionWithoutRecap",
    "SessionRecap",
    "Viewer",
    "ViewerName",
    "ViewerProfile",
    # rundown 域
    "DEFAULT_RUNDOWN",
    "Rundown",
    "RundownInstance",
    "RundownInstanceSegment",
    "RundownSegment",
    "RundownTransition",
]
