"""主播 Agent 流程单（Rundown）子系统子包。

Rundown 子系统是主播 Agent 的**内部契约与编排**（不跨 Agent 共享、不注册为工具）。

模块按数据流分层：

- ``rundown``       - 流程单数据契约（``Rundown`` / ``RundownSegment``）+ 内置默认流程单 ``DEFAULT_RUNDOWN``

切片 1（数据契约 + 存储）只交付 ``rundown.py``。运行时状态、工具、闹钟等组件
在后续切片内聚，本子包不预先暴露。
"""
