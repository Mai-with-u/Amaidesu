"""LLM 中立数据契约。

Engine 与 Client 之间的厂商无关数据模型：请求侧（``Message`` /
``ToolSpec`` / ``GenerateRequest``）与响应侧（``Response`` / ``Usage`` /
``ToolCall``）。

硬约束：本模块不得出现任何厂商 SDK 类型或厂商专有命名——各厂商协议的
形状差异（嵌套工具调用 dict、参数 schema 的叫法分歧等）由
``clients/<vendor>/`` 适配端在翻译层消化。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class TextPart(BaseModel):
    """文本内容片段"""

    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """图像内容片段（vision 用）

    ``image`` 为图像引用：HTTP(S) URL 或 data URL（base64 内嵌）。
    本地路径/原始字节由适配端在翻译时转换为 data URL，不进入中立契约。
    """

    type: Literal["image"] = "image"
    image: str


Part = Union[str, TextPart, ImagePart]
"""消息内容片段：裸字符串是文本的简写形式。"""


class ToolCall(BaseModel):
    """模型发起的一次工具调用（中立形状）

    注意这不是任何厂商协议的原始形状（如嵌套的
    ``{"function": {"name": ..., "arguments": ...}}`` dict）——协议形状
    由 ``clients/<vendor>/`` 适配端负责双向转换。
    ``arguments`` 已解析为对象；严格调用保留解析错误与原始文本供调用方自纠。
    """

    id: str = ""
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    # 严格调用保留失败的原始参数，调用方可以反馈重试，但不能执行自动补齐的半份数据。
    raw_arguments: Optional[str] = None
    arguments_error: Optional[str] = None


class Message(BaseModel):
    """对话消息（system 不在此列）

    system 提示词不作为 messages 列表成员，而是 Engine 方法的独立参数
    （``system=None``）——各厂商对 system 的承载位置不同（独立参数 /
    首条消息 / system 指令），由适配端决定翻译方式。

    role 限 user / assistant / tool；parts 依次表达文本与图像片段。
    assistant 既往发起的工具调用由 ``tool_calls`` 承载，role="tool" 的观察
    经 ``tool_call_id`` 关联回对应调用——多轮工具循环喂养上下文时两者缺一
    都会得到协议非法的消息序列（tool 消息必须回应带 tool_calls 的
    assistant），故随 role 一并建模，而非仅存在于各厂商协议形状中。
    """

    role: Literal["user", "assistant", "tool"]
    parts: List[Part] = Field(default_factory=list)
    tool_calls: List[ToolCall] = Field(default_factory=list)
    tool_call_id: Optional[str] = None


class ToolSpec(BaseModel):
    """工具声明（供模型选择的被动能力描述）

    ``parameters`` 为 JSON Schema 形式的参数定义。注意各厂商对该字段的
    叫法不同（``parameters`` / ``input_schema`` / ``functionDeclarations``
    内嵌签名）——翻译成各厂商协议形状的责任在适配端（Client），本模型
    取中立名。
    """

    name: str
    description: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    """一次调用的 token 消耗

    ``cache_hit_tokens`` / ``cache_miss_tokens`` 为可空（Optional）：
    None 表示 provider 未上报缓存信息，与"上报了 0"严格区分。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: Optional[int] = None
    cache_miss_tokens: Optional[int] = None


class GenerateRequest(BaseModel):
    """一次生成请求的中立描述（Engine 归一化后交给 Client）"""

    messages: List[Message] = Field(default_factory=list)
    system: Optional[str] = None
    tools: List[ToolSpec] = Field(default_factory=list)
    temperature: Optional[float] = None
    strict_tool_arguments: bool = False


class Response(BaseModel):
    """一次调用的完整响应（Engine 的统一返回类型）"""

    success: bool
    content: Optional[str] = None
    tool_calls: List[ToolCall] = Field(default_factory=list)
    usage: Optional[Usage] = None
    finish_reason: Optional[str] = None
    # 实际服务这次请求的 API 模型标识（可能与请求时的注册名不同）
    model: Optional[str] = None
    reasoning_content: Optional[str] = None
    error: Optional[str] = None
    # 本次调用的请求历史 ID（账本落库键）；失败路径同样回填
    request_id: str = ""


__all__ = [
    "TextPart",
    "ImagePart",
    "Part",
    "Message",
    "ToolSpec",
    "ToolCall",
    "Usage",
    "GenerateRequest",
    "Response",
]
