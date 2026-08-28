"""
工具契约数据类（Wave 3 / §1.5 定案）

- 全部 ``slots=True`` 减少内存占用
- 时刻/时长字段约定：``*_ms``（毫秒 int）；不在本模块涉及时间字段
- 多模态结果：``ResultBlock(kind)`` 支持 "text" / "image"
- ``kind``：sync / async（唯一判别维度）
- ``provider``：builtin / game（来源溯源）；"mcp" 预留枚举值，v2 决策架构移除 MCP 桥接后暂无实现
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# 工具判别维度（§1.5）：sync（gather 等齐）/ async（fire-and-forget + 事件回传）
Kind = Literal["sync", "async"]

# 工具来源溯源（§1.5 v2.17 定案，合并 MaiBot 的 provider_name+type 双字段）
Provider = Literal["builtin", "game", "mcp"]

# 多模态结果块种类
BlockKind = Literal["text", "image"]

# 默认异步结果事件名（前缀）；可定制（如 set_goal → tool.result.set_goal_feedback）
DEFAULT_RESULT_EVENT_PREFIX = "tool.result."


@dataclass(slots=True)
class ToolSpec:
    """工具规格（注册时声明；Planner 看到的就是这个）

    Attributes:
        name: 工具唯一名（同 Registry 内去重）
        description: LLM 看的工具描述
        parameters_schema: JSON Schema 形态的参数描述（主流共识）；可为 None
        kind: "sync"（gather 等齐结果）/ "async"（fire-and-forget）
        result_event: 异步工具结果事件名，默认 ``tool.result.<name>``；
                      可定制（如 set_goal → ``tool.result.set_goal_feedback``）
        provider: 来源溯源，"builtin" / "game"；"mcp" 预留枚举值，暂无实现
        output_schema: 可选的 JSON Schema 形态的输出描述
    """

    name: str
    description: str
    parameters_schema: Optional[Dict[str, Any]] = None
    kind: Kind = "sync"
    result_event: str = ""
    provider: Provider = "builtin"
    output_schema: Optional[Dict[str, Any]] = None

    def resolve_result_event(self) -> str:
        """返回实际的异步结果事件名（默认 ``tool.result.<name>``）。"""
        if self.result_event:
            return self.result_event
        return f"{DEFAULT_RESULT_EVENT_PREFIX}{self.name}"


@dataclass(slots=True)
class ResultBlock:
    """多模态结果块（当前轮给 LLM；历史中摘要化文本防膨胀）

    Attributes:
        kind: "text" / "image"
        text: 文本内容（``kind="text"`` 时使用）
        data: base64 编码数据（``kind="image"`` 时使用）
        mime_type: MIME 类型（``kind="image"`` 时使用，如 "image/png"）
    """

    kind: BlockKind = "text"
    text: str = ""
    data: str = ""
    mime_type: str = ""


@dataclass(slots=True)
class ToolExecutionResult:
    """工具执行结果（同步工具返回；异步工具仅作回传失败/异常）

    Attributes:
        tool_name: 工具名
        success: 是否成功（执行成功/失败，而非业务层面）
        content: 文本内容/历史摘要
        blocks: 多模态块列表（同步结果给 LLM；历史中已摘要化文本）
        error_message: 错误信息（success=False 时非空）
        structured_content: 结构化结果（如 dict，保留以备校验/未来使用）
        duration_ms: 执行耗时（毫秒）
        timestamp_ms: 完成时刻（毫秒）
    """

    tool_name: str
    success: bool
    content: str = ""
    blocks: List[ResultBlock] = field(default_factory=list)
    error_message: str = ""
    structured_content: Any = None
    duration_ms: int = 0
    timestamp_ms: int = 0


@dataclass(slots=True)
class ToolInvocation:
    """工具调用请求（Planner → Registry → 执行函数）

    Attributes:
        tool_name: 工具名（对应 ToolSpec.name）
        arguments: 参数（dict 形态；执行函数自行解析具体 schema）
        call_id: 调用 ID（用于一次性回合内配对；可空）
        invoked_at_ms: 调用时刻（毫秒）
        source: 调用方标识（如 Agent 名）
    """

    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    invoked_at_ms: int = 0
    source: str = ""


__all__ = [
    "Kind",
    "Provider",
    "BlockKind",
    "DEFAULT_RESULT_EVENT_PREFIX",
    "ToolSpec",
    "ResultBlock",
    "ToolExecutionResult",
    "ToolInvocation",
]
