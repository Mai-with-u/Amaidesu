"""用于将推理模型的思考链与最终响应内容分离。"""

import re
from enum import Enum


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
    if mode is ReasoningParseMode.NONE:
        return content, None

    if mode is ReasoningParseMode.NATIVE:
        return content, reasoning_content

    if mode is ReasoningParseMode.AUTO and reasoning_content:
        return content, reasoning_content

    match = _THINK_TAG_PATTERN.search(content)
    if match is None:
        return content, None

    cleaned_content = _THINK_TAG_PATTERN.sub("", content)
    return cleaned_content, match.group(1)
