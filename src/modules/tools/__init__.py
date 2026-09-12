"""
Amaidesu 工具层

本模块提供：
- ``ToolSpec`` / ``ToolInvocation`` / ``ToolExecutionResult`` 数据类
  （slots=True）
- ``ResultBlock`` 多模态结果块（text / image base64+mime）
- ``ToolRegistry`` 注册中心：按名分发 / 去重 / 失败兜底（不抛）
- ``ToolProvider`` Protocol：声明一组工具的统一来源（builtin/game/mcp 三类）
- ``bind_core_tools(registry, config)`` —— 装配核心工具包（见 bootstrap.py）

## 类型选型原则
- kind / provider / result_block.kind 等取值集合固定 → ``Literal``
- 是否同步 vs 异步：
  - "sync"：调用→执行→结果返回（gather 等齐）
  - "async"：调用→发送即受理；完成结果经 ``result_event`` 事件回传

## provider 来源溯源
- "builtin"：进程内框架内置（含 AgentControl、speak 等）
- "game"   ：玩家引擎 Agent 声明的工具（动态，list_tools 返回）
- "mcp"    ：MCP server 暴露的工具（见 ``src/modules/mcp/provider.py``）

## 失败兜底
``ToolRegistry.invoke()`` 永不抛异常：未知工具 → 失败 ToolExecutionResult；
执行异常 → 失败 ToolExecutionResult（带 error_message）。

## 注册路径（生产）
组合根（``main.py``）显式装配，**禁止**使用 ``default_tool_registry()``：

```python
from src.modules.tools import ToolRegistry
from src.modules.tools import bind_core_tools

registry = ToolRegistry()
bind_core_tools(registry, config=...)  # L2 Provider 注入
```

``default_tool_registry()`` / ``set_default_registry()`` 仅供旧测试兼容，
详见 ``src.modules.tools.registry`` 警告。
"""

from src.modules.tools.bootstrap import bind_core_tools
from src.modules.tools.health import ToolHealthMonitor
from src.modules.tools.tasks import TaskLedger, TaskTracker, resolve_tasks_config
from src.modules.tools.models import (
    ResultBlock,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs
from src.modules.tools.registry import ToolRegistry

__all__ = [
    # 数据契约
    "ToolSpec",
    "ToolInvocation",
    "ToolExecutionResult",
    "ResultBlock",
    # Provider 协议
    "ToolProvider",
    "make_provider_from_specs",
    # 返回值归一化助手（简单工具正典路径）
    "as_tool_impl",
    # 注册中心
    "ToolRegistry",
    # 熔断器探活服务
    "ToolHealthMonitor",
    # 异步任务基建（记录表 / 跟踪循环 / 配置兜底读取）
    "TaskLedger",
    "TaskTracker",
    "resolve_tasks_config",
    # 装配入口（生产路径）
    "bind_core_tools",
]
