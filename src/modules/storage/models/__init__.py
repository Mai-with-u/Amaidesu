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
from src.modules.storage.models.events import GameEventRecord, GameEventTypeCount
from src.modules.storage.models.memory import (
    SessionRecap,
    UnsettledSession,
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
from src.modules.storage.models.sessions import LiveSession, LiveSessionListItem
from src.modules.storage.models.sim import SimGift, SimPersona
from src.modules.storage.models.topics import TimelineSummaryEntry
from src.modules.storage.models.viewer_identity import PendingViewerSummary

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
    # events 域
    "GameEventRecord",
    "GameEventTypeCount",
    # memory 域
    "SessionRecap",
    "UnsettledSession",
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
    # sessions 域
    "LiveSession",
    "LiveSessionListItem",
    # sim 域
    "SimGift",
    "SimPersona",
    # topics 域
    "TimelineSummaryEntry",
    # viewer_identity 域
    "PendingViewerSummary",
]
