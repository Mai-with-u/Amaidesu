"""直播近况同步（把开播、换环节、收播、场次回顾与整场记录推给 MaiBot 桥接插件）。

- ``config_schema.py``：``[maibot_sync]`` 配置 Schema（infra.toml）
- ``reporter.py``：上报器（订阅事实事件 → HTTP 推送）
- ``transcript.py``：从库里读出一场直播的全部对话，整理成推给插件的整场记录
"""

from src.modules.maibot_sync.config_schema import MaiBotSyncConfigSchema
from src.modules.maibot_sync.reporter import HttpStatusSender, LiveStatusReporter, StatusSender, TranscriptSource
from src.modules.maibot_sync.transcript import StorageTranscriptSource

__all__ = [
    "HttpStatusSender",
    "LiveStatusReporter",
    "MaiBotSyncConfigSchema",
    "StatusSender",
    "StorageTranscriptSource",
    "TranscriptSource",
]
