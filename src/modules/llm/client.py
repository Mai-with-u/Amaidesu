"""LLM 客户端抽象与遗留共享响应模型。

Client 接口（Engine↔Client 契约）采用中立 payload：客户端接受
``GenerateRequest``、返回 ``payload.Response``。实现类在
``clients/<vendor>/`` 中注册到 ``clients`` 包的调度表（显式字典），
本模块不再承载任何注册表机制。

``LLMResponse`` 是遗留响应形状（消费方与既有测试尚未迁移），由
Engine 在新旧契约之间做双向适配；其退役随消费方迁移逐步完成。
"""

from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.modules.llm.payload import GenerateRequest, Response

OnDeltaCallback = Any
"""流式增量回调：(kind, text_delta)，kind ∈ {"reasoning", "content"}。"""


class LLMResponse(BaseModel):
    """LLM 响应结果（遗留形状）

    旧入口（``chat`` / ``chat_messages`` 等）与既有消费方仍依赖此形状；
    新入口统一返回 ``payload.Response``。两者之间的转换由 Engine 负责。
    """

    success: bool
    content: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    # 结束原因在新旧响应转换之间保留，避免把容量上限中断误判为正常交付。
    finish_reason: Optional[str] = None
    reasoning_content: Optional[str] = None
    error: Optional[str] = None
    # 本次调用的请求历史 ID（request_history_manager 落库键）。调用方（如
    # Planner 决策事件）用它作为"查看完整请求"的指针；失败路径同样回填。
    request_id: str = ""


class BaseLLMClient(abc.ABC):
    """LLM 客户端的最小统一接口（中立 payload 契约）。

    设计约定：客户端按 provider 维度共享（一个 provider 一个连接实例），
    model 由调用方每次请求显式传入——Engine 按 ``llm_profiles.<name>``
    的 ``model_list`` 做选择与故障切换。

    实现类经 ``clients`` 包的显式调度表接入（新增厂商 = 新增
    ``clients/<vendor>/`` 目录 + 调度表追加一行），本模块不做注册。
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """保存客户端的原始 provider 配置（连接信息/鉴权/超时/重试/默认 headers）。"""
        self.config = config

    @abc.abstractmethod
    async def generate(
        self,
        request: GenerateRequest,
        *,
        model: str,
        temperature: Optional[float] = None,
        on_delta: Optional[OnDeltaCallback] = None,
        interrupt_flag: Optional[Any] = None,
    ) -> Response:
        """执行一次聊天请求（中立契约）。

        ``model`` 必填：客户端不持有默认模型，由 Engine 按 profile 选定后传入。
        ``temperature`` 为 Engine 按 profile 档位填充的生成参数，
        请求内同名字段缺省时生效。
        ``on_delta`` 非 None 时实现方应走流式传输并逐帧回调增量，
        最终仍返回完整 Response（传输层流式、语义层整段）。
        实现方需要遵守 request 的输出额度省略与严格参数解析策略，
        并保留结束原因；未完成的工具参数必须交回错误，不能猜测补齐后执行。
        """
        raise NotImplementedError

    async def generate_vision(
        self,
        request: GenerateRequest,
        images: List[Any],
        *,
        model: str,
        temperature: Optional[float] = None,
        interrupt_flag: Optional[Any] = None,
    ) -> Response:
        """执行视觉请求（中立契约）；不支持时由默认实现明确报告。model 必填。"""
        raise NotImplementedError("该客户端不支持视觉请求")

    async def cleanup(self) -> None:
        """释放客户端资源；默认无需执行任何操作。"""
        return None
