"""主播 Agent 子包。

主播 Agent 是一体：Planner（决策核心）+ reply 工具（入口）+ Replyer（表达引擎）。

目录结构：
顶层平铺——Agent 内部件与协作组件（非工具，不注册进 ToolRegistry）：
- ``plan``              - 决策契约（DecisionPlan Pydantic，Planner 产出 / Replyer 消费）
- ``canonical``         - 对话 canonical 映射（live_chat 行/弹幕批 → 原生消息的单一序列化点）
- ``planner``           - 决策核心（决策循环，调 Planner profile）
- ``replyer``           - 表达引擎（调 Replyer profile + WordFilter）
- ``decision_executor`` - 决策轮执行器（两阶段决策执行半：round_id + stage/decision 事件 + 发言派发；调度半留 streamer_agent）
- ``speech_dispatcher`` - 发言管线编排（streamer.speech 事件 + TTS 队列生命周期 + 字幕/VTS 扇出）
- ``stats``             - 运行时统计（7 项计数器，Agent 与决策执行器共享）
- ``proactive_trigger`` - 主动发言纯规则触发器（主循环直接驱动）
- ``room_state``        - 直播间态势规则层（纯规则，60s 滑动窗口）
- ``message_buffer``    - 弹幕聚合缓冲（idle 补偿公式保留）
- ``timing_gate``       - 强制触发判定（is_forced）
- ``background``        - 后台双任务（轻循环记账 + 压缩 worker）
- ``streamer_agent``    - BaseAgent 子类（装配根：生命周期 + 工具注册 + 事件订阅 + 输入 + 调度半）

子包：
- ``rundown/``          - 流程单（Rundown）子系统：``rundown``（数据契约 + 内置默认流程单）/
  ``rundown_state``（运行时状态：游标 + 计时 + 唯一变更边界）/ ``rundown_tool``（Agent 推进工具）/
  ``presentation``（Dashboard 视图拼装 + 手动控制翻译，纯函数）
- ``tools/``            - Agent 专属工具壳层（**真工具**）：``reply_tool``（provider="streamer"）
  （reply / rundown_control，经 ToolRegistry 注册 + 名单隔离）；
  只包装顶层内部件，不含决策/表达逻辑
- ``command/``          - 命令接线（``router`` 识别+安全闸+委派组件；``command``/``command_parser``/``command_registry`` 纯解析原语，不注册工具）
"""

from src.agents.streamer.streamer_agent import StreamerAgent

__all__ = ["StreamerAgent"]
