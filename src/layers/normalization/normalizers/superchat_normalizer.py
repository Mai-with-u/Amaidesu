"""
醒目留言数据标准化器
"""

from typing import Optional
from .base import DataNormalizer
from src.core.base.raw_data import RawData
from src.core.base.normalized_message import NormalizedMessage
from src.layers.normalization.content import SuperChatContent


class SuperChatNormalizer(DataNormalizer):
    """醒目留言数据标准化器"""

    def can_handle(self, data_type: str) -> bool:
        return data_type == "superchat"

    @property
    def priority(self) -> int:
        return 100

    async def normalize(self, raw_data: RawData) -> Optional[NormalizedMessage]:
        """将醒目留言 RawData 转换为 NormalizedMessage"""
        content = raw_data.content
        if not isinstance(content, dict):
            return None

        structured_content = SuperChatContent(
            user=content.get("user", "未知用户"),
            content=content.get("content", ""),
            amount=content.get("price", content.get("amount", 0.0)),
        )

        metadata = raw_data.metadata.copy()
        metadata["source"] = raw_data.source
        metadata["original_timestamp"] = raw_data.timestamp

        return NormalizedMessage(
            text=structured_content.get_display_text(),
            content=structured_content,
            source=raw_data.source,
            data_type=raw_data.data_type,
            importance=structured_content.get_importance(),
            metadata=metadata,
            timestamp=raw_data.timestamp,
        )
