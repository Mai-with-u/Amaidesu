"""
结构化内容类型

导出所有StructuredContent实现和Discriminated Union。
"""

from typing import Annotated, Union
from pydantic import Field

from .base import StructuredContent
from .text_content import TextContent
from .gift_content import GiftContent
from .super_chat_content import SuperChatContent

# Discriminated Union - 根据 "type" 字段自动分发
ContentType = Annotated[Union[TextContent, GiftContent, SuperChatContent], Field(discriminator="type")]

__all__ = [
    "StructuredContent",
    "TextContent",
    "GiftContent",
    "SuperChatContent",
    "ContentType",
]
