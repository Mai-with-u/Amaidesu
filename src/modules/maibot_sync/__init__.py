"""与 MaiBot 适配器插件同步：直播近况、场次回顾与整场记录推过去，长期笔记发布过去、QQ 里记下的约定收回来。

- ``config_schema.py``：``[maibot_sync]`` 配置 Schema（infra.toml）
- ``reporter.py``：上报器（订阅事实事件 → HTTP 推送）
- ``transcript.py``：从库里读出一场直播的全部对话，整理成推给插件的整场记录
- ``notes_sync.py``：共享长期笔记（正本在本地，写完发布给插件，读前收下 QQ 里记下的约定）
"""

from src.modules.maibot_sync.config_schema import MaiBotSyncConfigSchema
from src.modules.maibot_sync.notes_sync import (
    HttpNotesRemote,
    NotesRemote,
    PendingPromise,
    SharedNotesStore,
)
from src.modules.maibot_sync.reporter import (
    HttpStatusSender,
    LiveStatusReporter,
    StatusRejected,
    StatusSender,
    TranscriptSource,
)
from src.modules.maibot_sync.transcript import StorageTranscriptSource

__all__ = [
    "HttpNotesRemote",
    "HttpStatusSender",
    "LiveStatusReporter",
    "MaiBotSyncConfigSchema",
    "NotesRemote",
    "PendingPromise",
    "SharedNotesStore",
    "StatusRejected",
    "StatusSender",
    "StorageTranscriptSource",
    "TranscriptSource",
]
