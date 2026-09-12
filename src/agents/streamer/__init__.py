"""主播 Agent 子包。

主播 Agent 是一体：Planner（决策核心）+ reply 工具（入口）+ Replyer（表达引擎）。

目录结构：
顶层平铺——Agent 内脏与协作组件（非工具，不注册进 ToolRegistry）：
- ``plan``              - 决策契约（DecisionPlan Pydantic，Planner 产出 / Replyer 消费）
- ``planner``           - 决策核心（决策循环，调 planner_llm）
- ``replyer``           - 表达引擎（调 replyer_llm + ProfanityFilter）
- ``proactive_trigger`` - 主动发言纯规则触发器（主循环直接驱动）
- ``room_state``        - 直播间态势规则层（纯规则，60s 滑动窗口）
- ``message_buffer``    - 弹幕聚合缓冲（idle 补偿公式保留）
- ``timing_gate``       - 强制触发判定（is_forced）
- ``background``        - 后台双任务（轻循环记账 + 压缩 worker）
- ``streamer_agent``    - BaseAgent 子类（编排上面所有组件）

子包：
- ``rundown/``          - 流程单（Rundown）子系统：``rundown``（数据契约 + 内置默认流程单）/
  ``rundown_state``（运行时状态：游标 + 计时 + 唯一变更边界）/ ``rundown_tool``（Agent 推进工具）
- ``tools/``            - Agent 专属工具壳层（**真工具**，provider="builtin"）：``reply_tool``
  （reply / rundown_control，经 ToolRegistry 注册 + 名单隔离）；
  只包装顶层内脏，不含决策/表达逻辑
- ``command/``          - 纯解析原语（命令数据结构 / 解析器 / 注册表，不注册工具）
"""

from src.agents.streamer.streamer_agent import StreamerAgent

__all__ = ["StreamerAgent"]
