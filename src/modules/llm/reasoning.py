"""用于将推理模型的思考链与最终响应内容分离。"""

import re
from enum import Enum

from src.modules.llm.payload import ReasoningFormat


class ReasoningParseMode(str, Enum):
    """推理内容解析策略。"""

    AUTO = "auto"
    NATIVE = "native"
    THINK_TAG = "think_tag"
    NONE = "none"


_THINK_TAG_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def parse_reasoning(
    content: str,
    reasoning_content: str | None,
    mode: ReasoningParseMode,
) -> tuple[str, str | None]:
    """按指定策略从响应中分离思考链，并返回清理后的内容与思考链。"""
    cleaned_content, reasoning, _ = split_reasoning(content, reasoning_content, mode)
    return cleaned_content, reasoning


def split_reasoning(
    content: str,
    reasoning_content: str | None,
    mode: ReasoningParseMode,
) -> tuple[str, str | None, ReasoningFormat | None]:
    """分离思考链，并记下它来自独立字段还是正文里的 ``<think>`` 标签。

    格式随思考一起保存：回传时按原格式写回，服务端看到的历史与它当初
    生成的一致。没有思考时格式为 None。
    """
    if mode is ReasoningParseMode.NONE:
        return content, None, None

    if mode is ReasoningParseMode.NATIVE:
        return content, reasoning_content, "field" if reasoning_content else None

    if mode is ReasoningParseMode.AUTO and reasoning_content:
        return content, reasoning_content, "field"

    match = _THINK_TAG_PATTERN.search(content)
    if match is None:
        return content, None, None

    cleaned_content = _THINK_TAG_PATTERN.sub("", content)
    return cleaned_content, match.group(1), "think_tag"
