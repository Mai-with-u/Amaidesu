"""主播 Agent 子包（Wave 6 / §1.4 定案）。

主播 Agent 是一体：Planner（决策核心）+ reply 工具（入口）+ Replyer（表达引擎）。

目录结构（子包化重组，git mv 保留历史）：
顶层平铺——Agent 内脏与协作组件（**不是工具**，永不注册为 ToolRegistry）：
- ``plan``              - 决策契约（DecisionPlan Pydantic，Planner 产出 / Replyer 消费）
- ``planner``           - Stage 1 决策核心（决策循环，调 planner_llm）
- ``replyer``           - Stage 2 表达引擎（调 replyer_llm + ProfanityFilter）
- ``proactive_trigger`` - 主动发言纯规则触发器（主循环直接驱动）
- ``room_state``        - 直播间态势规则层（纯规则，60s 滑动窗口）
- ``message_buffer``    - 弹幕聚合缓冲（idle 补偿公式保留）
- ``timing_gate``       - 仅保留 is_forced（强制触发判定）
- ``background``        - 后台双任务（轻循环记账 + 压缩 worker）
- ``streamer_agent``    - BaseAgent 子类（编排上面所有组件）

子包：
- ``agenda/``           - Agenda（节目单）子系统：``agenda``（数据契约）/ ``agenda_loader``
  （TOML 加载 + AI 扩展）/ ``agenda_state``（运行时状态机）/ ``agenda_store``（SQLite 实现）/
  ``agenda_idle``（后台调度循环），机制文档见 ``docs/architecture/agenda-mechanism.md``
- ``tools/``            - Agent 专属工具壳层（**真工具**，provider="builtin"）：``reply_tool``
  （reply）/ ``proactive_tool``（should_speak_proactively）/ ``command_tool``（parse_command）；
  只包装顶层内脏，不含决策/表达逻辑
- ``command/``          - 纯解析原语（命令数据结构 / 解析器 / 注册表，不注册工具）

Wave 6 删除（迁移到 ``src/agents/streamer/command/`` 保持纯解析）：
- ``amaidesu_decider`` wrapper - 拆解为 StreamerAgent 内部组件
- ``llm_decider`` - DISCARD（被 Agent chat loop 覆盖）
- ``replay_decider`` - DISCARD
- ``maibot_decider`` - DISCARD（2.0 决策=主播 Planner）
- ``command_decider`` - REWRITE→  Tool

旧 decision 阶段 Stage 框架（manager / registry / @decider 装饰器）整层删除，
事件名 ``decision.intent.generated`` / ``output.intent.*`` 不再被新路径消费。
"""

from src.agents.streamer.streamer_agent import StreamerAgent

__all__ = ["StreamerAgent"]
