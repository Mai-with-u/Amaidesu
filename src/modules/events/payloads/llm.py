"""
事件 Payload 定义：llm 域 / 调用上下文水位

- ``llm.context.used``：LLMManager 每次成功调用的收尾事实，payload 携带该次
  请求的上下文分段占用解剖（system / messages / tools 三段 token 与明细，
  估算口径见 ``src/modules/llm/context_meter.py``）——WebUI"上下文水位"
  弹出明细的实时刷新数据源。观察面终点广播，消费者不得触发新决策。
"""

from typing import List

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event


class ContextSectionItemPayload(BasePayload):
    """分段内明细行（一条工具 / 一组同角色消息）"""

    name: str = Field(..., description="明细行名称（工具名 / 消息角色）")
    tokens: int = Field(default=0, description="该明细行 token 数（校准后）")


class ContextSectionPayload(BasePayload):
    """一个上下文分段（system / messages / tools 之一）"""

    key: str = Field(..., description="分段键（system / messages / tools）")
    tokens: int = Field(default=0, description="该段 token 数（校准后展示值）")
    raw_tokens: int = Field(default=0, description="该段本地估算原值（校准前）")
    count: int = Field(default=0, description="条目数（消息条数 / 工具条数）")
    items: List[ContextSectionItemPayload] = Field(default_factory=list, description="段内明细行")


@register_event(CoreEvents.LLM_CONTEXT_USED)
class LLMContextUsedPayload(BasePayload):
    """
    LLM 调用上下文水位事件 Payload（一次成功调用一条）。

    Attributes:
        request_id: 请求历史指针（llm_requests 主键，懒取完整明细的键）
        profile_name: LLM 用途 profile 名（planner / replyer / minecraft 等）
        model_name: 实际服务本次请求的 API 模型标识
        context_window: 模型上下文窗口 token 总量（0 = 未配置，前端隐藏水位）
        api_prompt_tokens: API 回报的输入 token 精确总数（分段展示的校准基准）
        completion_tokens: API 回报的输出 token 数
        sections: 三段占用明细（key/tokens/count/items）
        calibrated: 分段是否经过 API 总数校准（False = 展示原始估算值）
    """

    request_id: str = Field(..., description="请求历史指针（llm_requests 主键）")
    profile_name: str = Field(default="", description="LLM 用途 profile 名")
    model_name: str = Field(default="", description="实际服务的 API 模型标识")
    context_window: int = Field(default=0, description="模型上下文窗口总量（0=未配置）")
    api_prompt_tokens: int = Field(default=0, description="API 回报的输入 token 总数")
    completion_tokens: int = Field(default=0, description="API 回报的输出 token 数")
    sections: List[ContextSectionPayload] = Field(
        default_factory=list,
        description="三段占用明细（system / messages / tools）",
    )
    calibrated: bool = Field(default=False, description="分段是否经过 API 总数校准")


__all__ = ["ContextSectionItemPayload", "ContextSectionPayload", "LLMContextUsedPayload"]
