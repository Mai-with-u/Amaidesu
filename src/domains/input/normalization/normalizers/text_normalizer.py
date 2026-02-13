"""
文本数据标准化器
"""

from typing import Optional

from src.domains.input.normalization.content import TextContent
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.base.raw_data import RawData

from .base import DataNormalizer


class TextNormalizer(DataNormalizer):
    """文本数据标准化器"""

    def can_handle(self, data_type: str) -> bool:
        return data_type == "text"

    @property
    def priority(self) -> int:
        return 100

    def __init__(self, pipeline_manager=None):
        """
        初始化文本标准化器

        Args:
            pipeline_manager: 可选的 PipelineManager，用于文本预处理
        """
        self.pipeline_manager = pipeline_manager

    async def normalize(self, raw_data: RawData) -> Optional[NormalizedMessage]:
        """将文本 RawData 转换为 NormalizedMessage"""
        # 从 content 中提取文本（支持多种格式）
        text = None
        message_base = None

        if isinstance(raw_data.content, dict):
            # 检查是否是 MessageBase 格式（来自 BiliDanmakuOfficialInputProvider 等）
            if "message" in raw_data.content:
                message_base = raw_data.content["message"]
                # 从 MessageBase.raw_message 提取文本
                if hasattr(message_base, "raw_message"):
                    text = message_base.raw_message
                else:
                    # 降级处理：使用字符串表示
                    text = str(message_base)
            else:
                # 普通字典格式，尝试获取 "text" 键
                text = raw_data.content.get("text", str(raw_data.content))
        else:
            # 纯文本格式
            text = str(raw_data.content)

        # 准备 metadata，包含原始 metadata
        metadata = raw_data.metadata.copy()
        metadata["source"] = raw_data.source
        metadata["original_timestamp"] = raw_data.timestamp

        # 从 MessageBase 中提取用户信息到 metadata
        if message_base and hasattr(message_base, "message_info"):
            message_info = message_base.message_info
            if hasattr(message_info, "user_info") and message_info.user_info:
                metadata["user_id"] = message_info.user_info.user_id
                metadata["user_nickname"] = getattr(message_info.user_info, "user_nickname", None)

            if hasattr(message_info, "group_info") and message_info.group_info:
                metadata["group_id"] = message_info.group_info.group_id
                metadata["group_name"] = getattr(message_info.group_info, "group_name", None)

        # 如果配置了 PipelineManager，使用 TextPipeline 处理文本
        if self.pipeline_manager:
            try:
                processed_text = await self.pipeline_manager.process_text(text, metadata)
                if processed_text is None:
                    # Pipeline 返回 None 表示丢弃该消息
                    return None
                text = processed_text
            except Exception as e:
                # 出错时使用原文本，不中断流程
                from src.modules.logging import get_logger

                logger = get_logger("TextNormalizer")
                logger.error(f"TextPipeline处理失败: {e}", exc_info=True)

        structured_content = TextContent(text=text)

        return NormalizedMessage(
            text=structured_content.get_display_text(),
            content=structured_content,
            source=raw_data.source,
            data_type=raw_data.data_type,
            importance=structured_content.get_importance(),
            metadata=metadata,
            timestamp=raw_data.timestamp,
        )
