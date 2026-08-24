"""主播 Agent 子包（Wave 6 / §1.4 定案）。

主播 Agent 是一体：Planner（决策核心）+ reply 工具（入口）+ Replyer（表达引擎）。

模块列表（Wave 6 一次性搬迁并重命名）：
- ``plan``             - 决策契约（DecisionPlan Pydantic）
- ``room_state``       - 直播间态势规则层（纯规则，60s 滑动窗口）
- ``message_buffer``   - 弹幕聚合缓冲（idle 补偿公式保留）
- ``timing_gate``      - 仅保留 is_forced（强制触发判定）
- ``proactive_trigger`` - 主动发言纯规则触发器
- ``planner``          - Stage 1 决策核心（**不是工具，是 Agent 内脏**）
- ``replyer``          - Stage 2 表达引擎（**不是工具，是 reply 工具的执行器**）
- ``reply_tool``       - reply 工具入口（**真工具**，调用 Replyer）
- ``should_speak_proactively`` - should_speak_proactively 工具（**真工具**）
- ``agenda``           - Agenda 数据契约（StreamOutline→Agenda 重命名）
- ``agenda_state``     - Agenda 运行时状态机 + Storage 适配
- ``agenda_loader``    - Agenda TOML 加载 + AI 扩展
- ``agenda_idle``      - Agenda 调度器（outline_scheduler→空转探测器/Agenda 调度）
- ``background``       - 后台双任务（轻循环记账 + 压缩 worker）
- ``streamer_agent``   - BaseAgent 子类（编排上面所有组件）

删除（迁移到 ``src/agents/streamer/command/`` 保持纯解析）：
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
