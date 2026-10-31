"""与 MaiBot 适配器插件同步：直播近况、场次回顾与整场记录推过去，长期笔记的正本放在插件。

- ``config_schema.py``：``[maibot_sync]`` 配置 Schema（infra.toml）
- ``reporter.py``：上报器（订阅事实事件 → HTTP 推送）
- ``transcript.py``：从库里读出一场直播的全部对话，整理成推给插件的整场记录
- ``notes_sync.py``：共享长期笔记（两边都用时正本放在插件、本地 MEMORY.md 当缓存）
"""

from src.modules.maibot_sync.config_schema import MaiBotSyncConfigSchema
from src.modules.maibot_sync.notes_sync import (
    HttpNotesRemote,
    NotesConflict,
    NotesRemote,
    RemoteNotes,
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
    "NotesConflict",
    "NotesRemote",
    "RemoteNotes",
    "SharedNotesStore",
    "StatusRejected",
    "StatusSender",
    "StorageTranscriptSource",
    "TranscriptSource",
]
