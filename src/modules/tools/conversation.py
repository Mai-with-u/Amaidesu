"""工具结果 → 对话观察的渲染契约

对话通道与 UI 通道分离：LLM 观察只看文本答案与轻量元数据；base64
二进制载荷（截图等）属于 UI 展示通道，经 ``tool.result`` 事件进 WebUI
工具卡。本模块承载"载荷 → 对话安全视图"（递归滤除二进制）与视觉调用
方的取图（工具结果图像块 → data URL；调用方存进 VisionImageStore 后只把
句柄挂到下一次请求尾部，历史里不出现内联大载荷，也不出现图片引用）。

判定刻意保守：长度超过 1KB、仅含 base64 字符集（A-Za-z0-9+/=）且无
空白字符，三者同时成立才替换。中文长文、URL、JSON 文本必然含空白或
标点（冒号、引号、花括号等都不在 base64 字符集内），不会误伤。
"""

from __future__ import annotations

import re
from typing import Any, List

from src.modules.tools.models import ToolExecutionResult

# base64 字符集（等号只出现在尾部填充，这里统一放进行内字符集，不做位置校验）
_BASE64_ALPHABET = re.compile(r"[A-Za-z0-9+/=]+")

# 超过该长度（字符）才视为二进制载荷；短编码（ID、校验值）原样保留
_MIN_PAYLOAD_CHARS = 1024


def _is_base64_payload(value: str) -> bool:
    """超长、纯 base64 字符集且无空白的字符串判为二进制载荷。"""
    return len(value) > _MIN_PAYLOAD_CHARS and _BASE64_ALPHABET.fullmatch(value) is not None


def _payload_placeholder(value: str) -> str:
    """生成占位符；KB 数按 base64 解码后的真实字节大小换算，不足 1KB 记 1。"""
    decoded_bytes = len(value) * 3 // 4
    return f"[图像 {max(1, decoded_bytes // 1024)} KB 已省略]"


def to_conversation_view(payload: Any) -> Any:
    """把工具结果载荷转成对话安全视图：二进制载荷替换为占位符。

    dict / list 递归处理并返回新容器（不改写原载荷——原
    structured_content 仍供 tool.result 事件通道完整使用）；str 按
    二进制载荷判定替换；其余类型原样返回。
    """
    if isinstance(payload, str):
        return _payload_placeholder(payload) if _is_base64_payload(payload) else payload
    if isinstance(payload, dict):
        return {key: to_conversation_view(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [to_conversation_view(item) for item in payload]
    return payload


def extract_image_data_urls(result: ToolExecutionResult) -> List[str]:
    """从工具结果的内容块提取图像为 data URL 列表（视觉调用方的观察用图）。

    blocks 是工具结果的对话内容通道（文本块 + 图像块）；产物为 OpenAI
    ``image_url`` 方言可直收的形态，经引擎消息归一化转中立 ImagePart。
    无图（或块缺数据）返回空列表。
    """
    urls: List[str] = []
    for block in result.blocks:
        if block.kind == "image" and block.data:
            mime = block.mime_type or "image/png"
            urls.append(f"data:{mime};base64,{block.data}")
    return urls


__all__ = ["to_conversation_view", "extract_image_data_urls"]
