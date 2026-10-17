"""主播记忆子包：场次回顾 + 观众画像。

主播不能从现场重新推出来、必须自己记住的东西只有两样，各自按自己的键索引：

- ``recap``   场次回顾——每场收播写一份总结（做了什么、答应过什么、聊过的梗、
  谁来得多），按场次时间序索引；开播后注入"上场回顾"，工具按"往前第 n 场"取
- ``roster``  观众画像——收播时为本场最活跃、互动量达门槛的几位常客更新一句
  印象，按 ``(platform, user_id)`` 索引；本批发言人有印象才注入，工具按昵称查
- ``tools``   两个 LLM 可调的真工具（``memory_recall_session`` / ``memory_recall_viewer``），
  随主播 Agent 注册、名单只对主播可见

当场的工作记忆（对话时间线、前情提要）不在这里——那是上下文分层的事。
存储表（``session_recaps`` / ``viewer_profiles``）与仓储在 ``src/modules/storage/``，
本包只放生成、注入与工具逻辑。
"""

from src.agents.streamer.memory.recap import SessionRecapWriter, format_recap
from src.agents.streamer.memory.roster import RegularsRosterWriter, collect_payment_material
from src.agents.streamer.memory.tools import MEMORY_PROVIDER_NAME, build_memory_tool_provider

__all__ = [
    "MEMORY_PROVIDER_NAME",
    "RegularsRosterWriter",
    "SessionRecapWriter",
    "build_memory_tool_provider",
    "collect_payment_material",
    "format_recap",
]
