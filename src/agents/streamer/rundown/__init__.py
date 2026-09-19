"""主播 Agent 流程单（Rundown）行为侧子包。

承载流程单的**运行时行为**：数据契约（``Rundown`` / ``RundownSegment`` /
``DEFAULT_RUNDOWN``）已下沉为持久化契约，位于
``src/modules/storage/models/rundown.py``，由本子包、storage 仓储与
Dashboard API 共同消费；控制工具提供者在 ``../tools/rundown_tool.py``。

- ``rundown_state`` - 流程单运行时状态机（唯一变更边界，``rundown.changed`` 事件源）
- ``presentation``  - 控制动作执行与流程单情境视图构建（供 Planner / 提示词消费）
"""
