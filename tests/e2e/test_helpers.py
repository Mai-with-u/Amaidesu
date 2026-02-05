"""
E2E Test Helper Functions

提供测试辅助函数，简化测试代码
"""
from src.core.base.normalized_message import NormalizedMessage
from src.core.base.raw_data import RawData
from src.domains.normalization.content import TextContent


def create_normalized_message(
    text: str,
    source: str = "test",
    importance: float = 0.5
) -> NormalizedMessage:
    """
    创建 NormalizedMessage 的辅助函数

    Args:
        text: 消息文本
        source: 消息来源
        importance: 重要性

    Returns:
        NormalizedMessage 实例
    """
    return NormalizedMessage(
        text=text,
        content=TextContent(text=text),
        source=source,
        data_type="text",
        importance=importance,
        metadata={},
        timestamp=None
    )


def create_raw_data(
    content: str,
    source: str = "test",
    data_type: str = "text"
) -> RawData:
    """
    创建 RawData 的辅助函数

    Args:
        content: 内容
        source: 来源
        data_type: 数据类型

    Returns:
        RawData 实例
    """
    return RawData(
        content=content,
        data_type=data_type,
        source=source,
        metadata={}
    )
