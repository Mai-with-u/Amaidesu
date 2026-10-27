"""LLM 中立数据契约。

Engine 与 Client 之间的厂商无关数据模型：请求侧（``Message`` /
``ToolSpec`` / ``GenerateRequest``）与响应侧（``Response`` / ``Usage`` /
``ToolCall``）。

硬约束：本模块不得出现任何厂商 SDK 类型或厂商专有命名——各厂商协议的
形状差异（嵌套工具调用 dict、参数 schema 的叫法分歧等）由
``clients/<vendor>/`` 适配端在翻译层消化。
"""

from __future__ import annotations

import json
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


class ImageRefPart(BaseModel):
    """图像引用片段——会话历史中图像的唯一合法形态

    ``ref`` 是 VisionImageStore 的进程内句柄；base64 本体不进历史，
    引擎装配期按新鲜度窗口注水为 :class:`ImagePart`，过期/逐出降级为
    提示文本。翻译层（client 适配端）见到本类型即结构缺陷——注水未
    执行就到了出网点，必须硬错而不是把句柄字符串发给服务端。
    """

    type: Literal["image_ref"] = "image_ref"
    ref: str


Part = Union[str, TextPart, ImagePart, ImageRefPart]
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


ReasoningFormat = Literal["field", "think_tag"]
"""思考内容在响应里的承载格式：独立字段，或正文开头的 ``<think>`` 标签。"""


class Reasoning(BaseModel):
    """模型某一步产出的思考，供工具循环内回传给同一个模型

    ``format`` 记录收到时的格式，写回时按原格式还原，服务端看到的历史与
    它当初生成的一致；``model`` 是产出它的模型注册名（``[[llm_models]].name``），
    引擎只把思考发给同一个模型，换到备用模型时这段思考不发。
    """

    text: str
    format: ReasoningFormat = "field"
    model: str = ""


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
    ``reasoning`` 只在 assistant 消息上有意义：该步思考，按模型开关决定是否发出。
    """

    role: Literal["user", "assistant", "tool"]
    parts: List[Part] = Field(default_factory=list)
    tool_calls: List[ToolCall] = Field(default_factory=list)
    tool_call_id: Optional[str] = None
    reasoning: Optional[Reasoning] = None


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
    # 上下文计量归属（只供监控分段，适配端不发给模型）：声明本身计入哪一段，
    # 以及这个工具的调用结果计入哪一段（None = 按默认归属）
    context_section: Optional[str] = None
    result_section: Optional[str] = None


class ContextPart(BaseModel):
    """系统提示词中单独计量的片段（只供监控分段，适配端不发给模型）

    计量器从系统提示词总量里扣出该片段，记到 ``section`` 段下名为 ``name``
    的明细行；``text`` 必须原样出现在系统提示词里，否则忽略。
    """

    section: str
    name: str
    text: str


class Usage(BaseModel):
    """一次调用的 token 消耗

    ``cache_hit_tokens`` / ``cache_miss_tokens`` / ``reasoning_tokens`` 为可空
    （Optional）：None 表示 provider 未上报该项，与"上报了 0"严格区分。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: Optional[int] = None
    cache_miss_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None


class GenerateRequest(BaseModel):
    """一次生成请求的中立描述（Engine 归一化后交给 Client）"""

    messages: List[Message] = Field(default_factory=list)
    system: Optional[str] = None
    tools: List[ToolSpec] = Field(default_factory=list)
    temperature: Optional[float] = None
    strict_tool_arguments: bool = False
    reasoning_effort: Optional[str] = None
    # 系统提示词中单独计量的片段（如技能目录），只供上下文分段统计
    system_parts: List[ContextPart] = Field(default_factory=list)


class Response(BaseModel):
    """一次调用的完整响应（Engine 的统一返回类型）"""

    success: bool
    # 理想形态是 string 或 null，但部分厂商 client 会透传结构化内容（如分段列表）；
    # 协议归一在 to_assistant_message 里完成（非 str 序列化为 JSON 字符串），这里不做截断
    content: Any = None
    tool_calls: List[ToolCall] = Field(default_factory=list)
    usage: Optional[Usage] = None
    finish_reason: Optional[str] = None
    # 实际服务这次请求的 API 模型标识（可能与请求时的注册名不同）
    model: Optional[str] = None
    reasoning_content: Optional[str] = None
    # 思考在响应里的承载格式（Client 解析时记录；无思考为 None）
    reasoning_format: Optional[ReasoningFormat] = None
    # 产出思考的模型开着回传开关时由 Engine 填写；调用方把它原样放进这一步的
    # assistant 消息（dict 消息用 ``reasoning`` 键），下一步请求即带回给同一个模型
    reasoning_echo: Optional[Reasoning] = None
    error: Optional[str] = None
    # 本次调用的请求历史 ID（账本落库键）；失败路径同样回填
    request_id: str = ""
    # 厂商原始 usage dict 的 JSON 序列化字符串：用于追溯重放（如解析字段更新后回填）；
    # 解析即弃链路唯一兜底，不做任何加工（包含未上报字段、嵌套对象原样保留）
    usage_raw_json: Optional[str] = None
    # 音频输出模态（generate_audio 路径）：base64 音频载荷与请求指定的封装格式；
    # 文本路径恒为 None。语音文本经 transcript 回填 content，不在此重复
    audio_base64: Optional[str] = None
    audio_format: Optional[str] = None

    @property
    def ok(self) -> bool:
        """调用是否成功（``success`` 的直取别名）。Response 是字段齐全的
        Pydantic 模型，调用方按契约直取字段，不需要 getattr 防御。"""
        return self.success


def response_to_assistant_message(response: Any) -> Dict[str, Any]:
    """把一次模型响应转回工具循环可继续喂养的 assistant 消息（对话补全线协议形状 dict）

    工具循环调用方收到响应后，要把它作为一条 assistant 消息追加进自己的
    消息历史，再作为下一轮 ``generate`` 的输入。这段转换承载三条线协议规则：
    content 非 str 时序列化为 JSON 字符串（协议要求 content 为 string 或
    null）；工具调用的参数必须回传 JSON 字符串而非对象（``ensure_ascii=False``
    保原文可读、``default=str`` 对不可序列化参数值兜底而非让整轮请求崩）；
    模型开着思考回传开关时，思考原样写进 ``reasoning`` 键带给同一个模型。
    该知识属于 LLM 模块——散在调用方则每个新工具循环都要重抄一遍，抄错
    一处即服务端 400。做成模块级函数而非 ``Response`` 方法：调用方（含既有
    测试替身）以鸭子类型持有响应对象，属性宽容读取让任何满足 ``Response``
    字段形状的对象都能转换。消息以 dict 形态返回，与调用方既有消息历史
    （含 system 等本契约之外的 role）的承载形态保持一致，调用方一行完成追加。
    """
    content: Any = getattr(response, "content", None)
    if content is not None and not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    tool_calls: List[ToolCall] = getattr(response, "tool_calls", None) or []
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False, default=str),
                },
            }
            for call in tool_calls
        ]
    reasoning_echo: Optional[Reasoning] = getattr(response, "reasoning_echo", None)
    if reasoning_echo is not None:
        message["reasoning"] = reasoning_echo.model_dump()
    return message


__all__ = [
    "TextPart",
    "ImagePart",
    "ImageRefPart",
    "Part",
    "ReasoningFormat",
    "Reasoning",
    "Message",
    "ToolSpec",
    "ContextPart",
    "ToolCall",
    "Usage",
    "GenerateRequest",
    "Response",
    "response_to_assistant_message",
]
