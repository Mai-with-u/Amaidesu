"""主播 Agent 专属工具壳层（Agent 内部 Provider）。

- ``reply_tool``        - ``streamer_reply`` 工具入口（包装 Replyer 表达引擎；
  注册进 ToolRegistry，名单 ["streamer"]）
- ``rundown_tool``      - ``rundown_control`` 工具（provider="rundown"；决策面
  按流程单激活状态条件追加）

should_speak_proactively / parse_command 是代码直连的内部件（不是工具，
不注册）；主动发言判定在 ``../proactive_trigger.py``，命令解析原语在
``../command/``。
"""

from .reply_tool import build_reply_tool_spec
from .rundown_tool import build_rundown_tool_provider

__all__ = [
    "build_reply_tool_spec",
    "build_rundown_tool_provider",
]
