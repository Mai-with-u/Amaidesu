"""主播 Agent Agenda（节目单）子系统子包。

Agenda 子系统是主播 Agent 的**内部契约与编排**（不跨 Agent 共享、不注册为工具），
机制文档见 ``docs/architecture/agenda-mechanism.md``。

模块按数据流分层：
- ``agenda``          - 节目单数据契约（Agenda/AgendaSegment/AgendaBranch + TOML 解析）
- ``agenda_loader``   - 节目单加载 + 每环节 AI 扩展（独立 LLM profile ``llm_agenda``）
- ``agenda_state``    - 运行时状态机 + AgendaStore 协议（暂停/跳转/回退/手动覆盖）
- ``agenda_store``    - AgendaStore 协议的 SQLite 实现
- ``agenda_idle``     - 后台调度循环（空转探测器/Agenda 调度，tick 推进 + AI 评估）

子包内同目录模块互相以相对 import 引用（移动后无需改动）。
"""

from .agenda import Agenda, AgendaBranch, AgendaSegment, parse_agenda_toml
from .agenda_idle import AgendaIdle
from .agenda_loader import AgendaLoader, ExpandedSegment
from .agenda_state import AgendaRuntimeRow, AgendaState, AgendaStatus, AgendaStore
from .agenda_store import SQLiteAgendaStore

__all__ = [
    "Agenda",
    "AgendaBranch",
    "AgendaSegment",
    "parse_agenda_toml",
    "AgendaIdle",
    "AgendaLoader",
    "ExpandedSegment",
    "AgendaRuntimeRow",
    "AgendaState",
    "AgendaStatus",
    "AgendaStore",
    "SQLiteAgendaStore",
]
