"""
Amaidesu 工具层（Wave 3 新增）

本模块提供：
- ``ToolSpec`` / ``ToolInvocation`` / ``ToolExecutionResult`` 数据类
  （slots=True，对齐 §1.5 定案）
- ``ResultBlock`` 多模态结果块（text / image base64+mime）
- ``ToolRegistry`` 注册中心：按名分发 / 去重 / 失败兜底（不抛）
- ``ToolProvider`` Protocol：声明一组工具的统一来源（builtin/game/mcp）
- ``@tool`` 装饰器（进程内轻量声明）

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
- "mcp"    ：MCP Provider 唯一协议适配点

## 失败兜底
``ToolRegistry.invoke()`` 永不抛异常：未知工具 → 失败 ToolExecutionResult；
执行异常 → 失败 ToolExecutionResult（带 error_message）。
"""

from src.modules.tools.models import (
    ResultBlock,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import ToolProvider, make_provider_from_specs
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.decorator import tool

__all__ = [
    "ToolSpec",
    "ToolInvocation",
    "ToolExecutionResult",
    "ResultBlock",
    "ToolProvider",
    "make_provider_from_specs",
    "ToolRegistry",
    "tool",
]
