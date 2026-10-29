"""主播记忆子包：场次回顾 + 观众画像 + 长期记忆 + 本场备忘录。

- ``recap``   场次回顾——每场收播写一份总结（做了什么、答应过什么、聊过的梗、
  谁来得多），按场次时间序索引；开播后注入"上场回顾"，工具按"往前第 n 场"取
- ``roster``  观众画像——收播时为本场最活跃、互动量达门槛的几位常客更新一句
  印象，按 ``(platform, user_id)`` 索引；本批发言人有印象才注入，工具按昵称查
- ``long_memory`` 长期记忆——主播自己整理的跨场次笔记（MEMORY.md：答应过的事、
  老梗、长线目标、对自己的认识），收播时把本场回顾与备忘录合并进去，开播时整份
  注入；正式场与彩排场各一份
- ``notebook`` 本场备忘录——模型经 ``streamer_notebook`` 工具主动写的会话内
  工作笔记（单条自由文本、全量覆盖写、收播清空不入库），每轮注入决策与表达
  两层上下文，收播时全文折叠进场次回顾与长期记忆的原料
- ``tools``   LLM 可调的真工具（``memory_recall_session`` / ``memory_recall_viewer``
  / ``streamer_notebook``），随主播 Agent 注册、名单只对主播可见

当场的工作记忆（对话时间线、前情提要）不在这里——那是上下文分层的事。
存储表（``session_recaps`` / ``viewer_profiles``）、仓储与长期记忆文件存储在
``src/modules/storage/``，本包只放生成、注入与工具逻辑（备忘录是唯一不落盘的成员：
会话作用域，进程内存活）。
"""

from src.agents.streamer.memory.long_memory import LongMemoryWriter, format_long_memory
from src.agents.streamer.memory.notebook import StreamerNotebook
from src.agents.streamer.memory.recap import SessionRecapWriter, format_recap
from src.agents.streamer.memory.roster import RegularsRosterWriter, collect_payment_material
from src.agents.streamer.memory.tools import (
    MEMORY_PROVIDER_NAME,
    NOTEBOOK_TOOL_FULL_NAME,
    build_memory_tool_provider,
    build_notebook_tool_provider,
    emit_notebook_changed,
)

__all__ = [
    "LongMemoryWriter",
    "MEMORY_PROVIDER_NAME",
    "NOTEBOOK_TOOL_FULL_NAME",
    "RegularsRosterWriter",
    "SessionRecapWriter",
    "StreamerNotebook",
    "build_memory_tool_provider",
    "build_notebook_tool_provider",
    "collect_payment_material",
    "emit_notebook_changed",
    "format_long_memory",
    "format_recap",
]
