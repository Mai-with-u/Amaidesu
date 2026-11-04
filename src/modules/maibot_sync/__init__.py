"""与 MaiBot 适配器插件同步：直播近况与整场记录推过去。

- ``config_schema.py``：``[maibot_sync]`` 配置 Schema（infra.toml）
- ``reporter.py``：上报器（订阅事实事件 → HTTP 推送；整场记录的"已推荐"标记与启动补推）
- ``transcript.py``：从库里读出一场直播的全部对话，整理成推给插件的整场记录
"""

from src.modules.maibot_sync.config_schema import MaiBotSyncConfigSchema
from src.modules.maibot_sync.reporter import (
    HttpStatusSender,
    LiveStatusReporter,
    StatusRejected,
    StatusSender,
    TranscriptPushLog,
    TranscriptSource,
)
from src.modules.maibot_sync.transcript import StorageTranscriptSource, TranscriptCandidate

__all__ = [
    "HttpStatusSender",
    "LiveStatusReporter",
    "MaiBotSyncConfigSchema",
    "StatusRejected",
    "StatusSender",
    "StorageTranscriptSource",
    "TranscriptCandidate",
    "TranscriptPushLog",
    "TranscriptSource",
]
