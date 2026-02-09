"""
文本数据标准化器
"""

from typing import Optional
from .base import DataNormalizer
from src.core.base.raw_data import RawData
from src.core.base.normalized_message import NormalizedMessage
from src.domains.input.normalization.content import TextContent


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
        # 从 content 中提取文本（支持字典格式和纯文本）
        if isinstance(raw_data.content, dict):
            text = raw_data.content.get("text", str(raw_data.content))
        else:
            text = str(raw_data.content)

        # 如果配置了 PipelineManager，使用 TextPipeline 处理文本
        if self.pipeline_manager:
            try:
                metadata = raw_data.metadata.copy()
                processed_text = await self.pipeline_manager.process_text(text, metadata)
                if processed_text is None:
                    # Pipeline 返回 None 表示丢弃该消息
                    return None
                text = processed_text
            except Exception as e:
                # 出错时使用原文本，不中断流程
                from src.core.utils.logger import get_logger

                logger = get_logger("TextNormalizer")
                logger.error(f"TextPipeline处理失败: {e}", exc_info=True)

        structured_content = TextContent(text=text)

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
