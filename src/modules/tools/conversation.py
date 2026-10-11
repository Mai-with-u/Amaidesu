"""工具结果 → 对话观察的渲染契约

对话通道与 UI 通道分离：LLM 观察只看文本答案与轻量元数据；base64
二进制载荷（截图等）属于 UI 展示通道，经 ``tool.result`` 事件进 WebUI
工具卡。本模块只负责"载荷 → 对话安全视图"这一件事——递归遍历工具
结果载荷，把"超长且形似 base64"的字符串值替换为占位符。

判定刻意保守：长度超过 1KB、仅含 base64 字符集（A-Za-z0-9+/=）且无
空白字符，三者同时成立才替换。中文长文、URL、JSON 文本必然含空白或
标点（冒号、引号、花括号等都不在 base64 字符集内），不会误伤。
"""

from __future__ import annotations

import re
from typing import Any

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


__all__ = ["to_conversation_view"]
