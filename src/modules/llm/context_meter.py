"""上下文分段计量器：把一次 LLM 调用的输入拆成 system / messages / tools 三段估算 token。

定位是监控估算，不是计费口径。精确总数永远以 API 回报的 ``prompt_tokens``
为准（落库账本同一来源）；本模块的本地估算只承担一件事——决定精确总数在
三段之间怎么分配：估算出各段原值后，按 ``api_prompt_tokens / 本地总和`` 的
校准系数等比缩放，估算误差因此不进入展示总数，也不会跨调用累积。

本地计数用 tiktoken（cl100k_base），惰性加载：词表文件首次使用时才拉取并
缓存，拉取失败（离线等）退化到 CJK 加权字符启发式。词表对非 OpenAI 模型
只是近似，但监控面板要求的量级与分段相对占比正确性在这个精度下成立。

图像内容不进 tokenizer，按单张固定估值计入所在消息段（vision 精确计价
随厂商与分辨率变化，监控场景不值得为其引入公式表）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.modules.logging import get_logger

logger = get_logger("ContextMeter")

# OpenAI chat 协议每条消息的固定包装开销（<im_start>/<role>/... 的量级近似）
_TOKENS_PER_MESSAGE = 4
# 单张图像的保守 token 估值（低分辨率档位量级，仅监控口径）
_IMAGE_TOKEN_ESTIMATE = 1000
# 字符启发式系数：CJK 字符约 0.6 token/字，ASCII 约 0.25 token/字符
_CJK_TOKEN_RATIO = 0.6
_ASCII_TOKEN_RATIO = 0.25
# CJK 统一表意区起点（含部首/标点扩展），之下的字符按 ASCII 密度计
_CJK_CODEPOINT_START = 0x2E80

# 分段键的封闭集合（payload/前端按此渲染）
SECTION_SYSTEM = "system"
SECTION_MESSAGES = "messages"
SECTION_TOOLS = "tools"

_encoding: Any = None
_encoding_failed = False


class ContextSectionItem(BaseModel):
    """分段内的明细行（一条工具 / 一组同角色消息）"""

    name: str
    tokens: int = 0


class ContextSection(BaseModel):
    """一个上下文分段（system / messages / tools 之一）"""

    key: str
    # 校准后展示值（calibrated=False 时等于 raw_tokens）
    tokens: int = 0
    # 本地估算原值（校准分量的分子）
    raw_tokens: int = 0
    # 条目数：system 恒 1；messages 为消息条数；tools 为工具条数
    count: int = 0
    items: List[ContextSectionItem] = Field(default_factory=list)


class ContextBreakdown(BaseModel):
    """一次成功调用的上下文占用解剖（落库 ``llm_requests.breakdown_json`` 与事件共用形状）"""

    request_id: str
    profile_name: str = ""
    model_name: str = ""
    # API 回报的输入 token 精确总数；0 = 上游未回报（此时展示原始估算值）
    api_prompt_tokens: int = 0
    # 本地估算三段总和（校准分母；与 api_prompt_tokens 的比值即校准系数）
    local_total_tokens: int = 0
    calibrated: bool = False
    sections: List[ContextSection] = Field(default_factory=list)


def _get_encoding() -> Any:
    """惰性取 tiktoken 编码器；不可用返回 None（进程内只尝试一次）。"""
    global _encoding, _encoding_failed
    if _encoding is not None or _encoding_failed:
        return _encoding
    try:
        # 可选重型依赖延迟加载：词表首次拉取有网络与启动开销，失败退化启发式
        import tiktoken

        _encoding = tiktoken.get_encoding("cl100k_base")
    except Exception as exc:  # noqa: BLE001 词表拉取失败等环境问题不应阻断调用链
        _encoding_failed = True
        logger.warning(f"tiktoken 编码器不可用，上下文分段退化字符启发式: {exc}")
    return _encoding


def count_text_tokens(text: str) -> int:
    """估算一段文本的 token 数（tiktoken 优先，启发式兜底）。"""
    if not text:
        return 0
    encoding = _get_encoding()
    if encoding is not None:
        try:
            return len(encoding.encode(text))
        except Exception as exc:  # noqa: BLE001 超长/非法码点等按启发式兜底
            logger.debug(f"tiktoken 编码失败，退化为启发式: {exc}")
    return _heuristic_tokens(text)


def _heuristic_tokens(text: str) -> int:
    """CJK 加权字符启发式：中文字符密度高、ASCII 密度低，分别计权后取整。"""
    cjk = sum(1 for ch in text if ord(ch) >= _CJK_CODEPOINT_START)
    ascii_chars = len(text) - cjk
    return max(1, int(cjk * _CJK_TOKEN_RATIO + ascii_chars * _ASCII_TOKEN_RATIO))


def _count_content_tokens(content: Any) -> int:
    """消息 content 计数：字符串走 tokenizer，多模态 part 列表逐段累计。"""
    if content is None:
        return 0
    if isinstance(content, str):
        return count_text_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += count_text_tokens(part)
            elif isinstance(part, dict):
                part_type = str(part.get("type") or "")
                if "image" in part_type:
                    total += _IMAGE_TOKEN_ESTIMATE
                elif isinstance(part.get("text"), str):
                    total += count_text_tokens(part["text"])
                else:
                    total += count_text_tokens(json.dumps(part, ensure_ascii=False))
        return total
    return count_text_tokens(json.dumps(content, ensure_ascii=False, default=str))


def _count_message_tokens(message: Any) -> int:
    """单条消息 token 计数（content / parts 双形状 + 工具调用 + 协议开销）。

    遗留 dict 路径文本在 ``content``；中立 payload dump 路径文本在 ``parts``
    （TextPart/ImagePart）。图像（含 data URL 的 base64 内嵌）不进 tokenizer，
    按固定估值计；assistant 既往工具调用按其 JSON 序列化计数。
    """
    if isinstance(message, dict):
        content = message.get("content")
        parts = message.get("parts") or []
        tool_calls = message.get("tool_calls") or []
    else:
        content = getattr(message, "content", None)
        parts = getattr(message, "parts", None) or []
        tool_calls = getattr(message, "tool_calls", None) or []

    total = _count_content_tokens(content)
    for part in parts:
        if isinstance(part, str):
            total += count_text_tokens(part)
        elif isinstance(part, dict):
            if str(part.get("type") or "") == "image":
                total += _IMAGE_TOKEN_ESTIMATE
            elif isinstance(part.get("text"), str):
                total += count_text_tokens(part["text"])
            else:
                total += count_text_tokens(json.dumps(part, ensure_ascii=False))
    if tool_calls:
        total += count_text_tokens(json.dumps(tool_calls, ensure_ascii=False, default=str))
    return total + _TOKENS_PER_MESSAGE


def _normalize_tool_spec(spec: Any) -> Tuple[str, Dict[str, Any]]:
    """把 ToolSpec 对象 / OpenAI 风格 dict / 中立 dict 统一成 (名称, 可序列化 dict)。"""
    if isinstance(spec, dict):
        function = spec.get("function")
        if isinstance(function, dict):
            return str(function.get("name") or "unknown"), spec
        return str(spec.get("name") or "unknown"), spec
    name = getattr(spec, "name", None)
    if name is not None:
        dump = spec.model_dump() if hasattr(spec, "model_dump") else {"name": str(name)}
        return str(name), dump
    return "unknown", {"repr": str(spec)}


def _count_tool_tokens(spec: Any) -> Tuple[str, int]:
    """单个工具声明的 token 计数（schema 全量 JSON 序列化后计数）。"""
    name, dump = _normalize_tool_spec(spec)
    return name, count_text_tokens(json.dumps(dump, ensure_ascii=False, default=str))


def extract_request_sections(kwargs: Dict[str, Any]) -> Optional[Tuple[str, List[Any], List[Any]]]:
    """从 generate 调用 kwargs 提取 (system, messages, tools) 三段原值。

    与 ``LLMManager._build_request_params`` 同构的双路径：中立 payload 契约
    路径从 ``kwargs["request"]`` 归一化出 dict 形状，遗留路径直接取 dict 列表。
    消息列表缺失（无法构成一次请求）返回 None。
    """
    request = kwargs.get("request")
    if request is not None:
        messages = [m.model_dump() for m in getattr(request, "messages", []) or []]
        tools = [t.model_dump() for t in getattr(request, "tools", []) or []]
        return getattr(request, "system", None) or "", messages, tools
    messages = kwargs.get("messages")
    if messages is None:
        return None
    return kwargs.get("system") or "", list(messages), list(kwargs.get("tools") or [])


def _role_item_name(role: str, count: int) -> str:
    """messages 明细行的显示名：按角色聚合，多于一 条时附条数。"""
    return f"{role} ×{count}" if count > 1 else role


def estimate_breakdown(
    *,
    request_id: str,
    profile_name: str,
    model_name: str,
    system: str,
    messages: List[Any],
    tools: List[Any],
    api_prompt_tokens: int = 0,
) -> ContextBreakdown:
    """估算一次调用的三段占用并按 API 总数校准（纯函数，不产生 IO）。"""
    system_tokens = count_text_tokens(system)
    message_tokens_by_role: Dict[str, int] = {}
    message_count_by_role: Dict[str, int] = {}
    for message in messages:
        role = str(
            message.get("role") if isinstance(message, dict) else getattr(message, "role", "unknown") or "unknown"
        )
        message_tokens_by_role[role] = message_tokens_by_role.get(role, 0) + _count_message_tokens(message)
        message_count_by_role[role] = message_count_by_role.get(role, 0) + 1
    tool_counts = [_count_tool_tokens(spec) for spec in tools]

    raw_sections = [
        ContextSection(
            key=SECTION_SYSTEM,
            raw_tokens=system_tokens,
            count=1 if system else 0,
            items=[ContextSectionItem(name="system", tokens=system_tokens)] if system else [],
        ),
        ContextSection(
            key=SECTION_MESSAGES,
            raw_tokens=sum(message_tokens_by_role.values()),
            count=len(messages),
            items=[
                ContextSectionItem(name=_role_item_name(role, message_count_by_role[role]), tokens=tokens)
                for role, tokens in message_tokens_by_role.items()
            ],
        ),
        ContextSection(
            key=SECTION_TOOLS,
            raw_tokens=sum(tokens for _, tokens in tool_counts),
            count=len(tool_counts),
            items=[ContextSectionItem(name=name, tokens=tokens) for name, tokens in tool_counts],
        ),
    ]

    local_total = sum(section.raw_tokens for section in raw_sections)
    calibrated = api_prompt_tokens > 0 and local_total > 0
    if calibrated:
        scale = api_prompt_tokens / local_total
        for section in raw_sections:
            section.tokens = int(round(section.raw_tokens * scale))
            for item in section.items:
                item.tokens = int(round(item.tokens * scale))
    else:
        for section in raw_sections:
            section.tokens = section.raw_tokens

    return ContextBreakdown(
        request_id=request_id,
        profile_name=profile_name,
        model_name=model_name,
        api_prompt_tokens=api_prompt_tokens,
        local_total_tokens=local_total,
        calibrated=calibrated,
        sections=raw_sections,
    )
