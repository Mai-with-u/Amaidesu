"""
Amaidesu 工具层（Wave 3 新增）

本模块提供：
- ``ToolSpec`` / ``ToolInvocation`` / ``ToolExecutionResult`` 数据类
  （slots=True，对齐 §1.5 定案）
- ``ResultBlock`` 多模态结果块（text / image base64+mime）
- ``ToolRegistry`` 注册中心：按名分发 / 去重 / 失败兜底（不抛）
- ``ToolProvider`` Protocol：声明一组工具的统一来源（builtin/game；"mcp" 预留枚举值，暂无实现）
- ``@tool`` 装饰器（双模式：pending 表 / 显式 registry）
- ``bind_pending_tools(registry)`` —— 把 pending 表刷入 registry
- ``bind_core_tools(registry, config)`` —— 装配核心工具包（见 bootstrap.py）

权威参考：
- 架构主文档 .omo/drafts/amaidesu-v2-architecture.md §1.5 / §1.5.1
- MaiBot 参考：ToolProvider 契约（MaiBot 提取）

## 类型选型原则
- kind / provider / result_block.kind 等取值集合固定 → ``Literal``
- 是否同步 vs 异步（§1.5 唯一判别维度）：
  - "sync"：调用→执行→结果返回（gather 等齐）
  - "async"：调用→发送即受理；完成结果经 ``result_event`` 事件回传

## provider 来源溯源
- "builtin"：进程内框架内置（含 AgentControl、speak 等）
- "game"   ：玩家引擎 Agent 声明的工具（动态，list_tools 返回）
- "mcp"    ：预留枚举值（v2 决策架构移除 MCP 桥接后暂无实现；保留以备未来重启；
            详见 .omo/audits/2026-08-27-unfinished-systems-audit.md D2）

## 失败兜底
``ToolRegistry.invoke()`` 永不抛异常：未知工具 → 失败 ToolExecutionResult；
执行异常 → 失败 ToolExecutionResult（带 error_message）。

## 注册路径（生产）
组合根（``main.py``）按以下顺序装配，**禁止**使用 ``default_tool_registry()``：

```python
from src.modules.tools import ToolRegistry
from src.modules.tools import bind_pending_tools, bind_core_tools

registry = ToolRegistry()
bind_core_tools(registry, config=...)  # L2 Provider 注入
bind_pending_tools(registry)  # L1 @tool pending 刷入
```

``default_tool_registry()`` / ``set_default_registry()`` 仅供旧测试兼容，
详见 ``src.modules.tools.registry`` 警告。
"""

from src.modules.tools.bootstrap import bind_core_tools
from src.modules.tools.decorator import bind_pending_tools, tool
from src.modules.tools.models import (
    ResultBlock,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import ToolProvider, make_provider_from_specs
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
    # 注册中心
    "ToolRegistry",
    # L1 装饰器
    "tool",
    # 装配入口（生产路径）
    "bind_pending_tools",
    "bind_core_tools",
]
