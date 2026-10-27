"""LLM 客户端抽象与共享响应模型。

Client 接口（Engine↔Client 契约）采用中立 payload：客户端接受
``GenerateRequest``、返回 ``payload.Response``，实现体内不再维护
其他响应形状。实现类在 ``clients/<vendor>/`` 中注册到 ``clients``
包的调度表（显式字典），本模块不承载任何注册表机制。
"""

from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional

from src.modules.llm.payload import GenerateRequest, Response

OnDeltaCallback = Any
"""流式增量回调：(kind, text_delta)，kind ∈ {"reasoning", "content"}。"""


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
        on_delta: Optional[OnDeltaCallback] = None,
        interrupt_flag: Optional[Any] = None,
    ) -> Response:
        """执行一次聊天请求（中立契约）。

        ``model`` 必填：客户端不持有默认模型，由 Engine 按 profile 选定后传入。
        生成参数（温度/思考强度档位）只从 ``request`` 同名字段取——Engine 已在
        单点完成"显式参数 > 请求字段 > profile 档默认值"的定序并写进请求对象，
        实现方不再自己做兜底合并。``on_delta`` 非 None 时实现方应走流式传输并
        逐帧回调增量，最终仍返回完整 Response（传输层流式、语义层整段）。
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
        interrupt_flag: Optional[Any] = None,
    ) -> Response:
        """执行视觉请求（中立契约）；不支持时由默认实现明确报告。model 必填。"""
        raise NotImplementedError("该客户端不支持视觉请求")

    async def generate_audio(
        self,
        request: GenerateRequest,
        *,
        model: str,
        voice: str,
        audio_format: str = "wav",
        interrupt_flag: Optional[Any] = None,
    ) -> Response:
        """执行音频输出请求（中立契约）；不支持时由默认实现明确报告。model 必填。

        ``voice`` / ``audio_format`` 为厂商词表的音色与封装格式；语音文本由
        服务端 transcript 经 ``content`` 回填，音频字节经 ``audio_base64``
        携带。仅支持非流式（音频模态不走增量回调路径）。
        """
        raise NotImplementedError("该客户端不支持音频输出请求")

    async def cleanup(self) -> None:
        """释放客户端资源；默认无需执行任何操作。"""
        return None
