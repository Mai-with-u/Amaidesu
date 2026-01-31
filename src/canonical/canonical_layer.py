"""
CanonicalLayer - Layer 2→3 桥接

负责将NormalizedText转换为CanonicalMessage，完成 Layer 2 到 Layer 3 的桥接。
订阅 normalization.text.ready 事件，发布 canonical.message_ready 事件。

数据流:
    InputLayer → normalization.text.ready → CanonicalLayer → canonical.message_ready → DecisionProvider
"""

from typing import Dict, Any, Optional, TYPE_CHECKING

from src.core.data_types.normalized_text import NormalizedText
from src.canonical.canonical_message import CanonicalMessage
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.pipeline_manager import PipelineManager


class CanonicalLayer:
    """
    Layer 2→3 桥接层

    职责:
    - 订阅 normalization.text.ready 事件
    - （可选）调用 PipelineManager.process_text 进行文本预处理
    - 将 NormalizedText 转换为 CanonicalMessage
    - 发布 canonical.message_ready 事件

    Attributes:
        event_bus: 事件总线实例
        pipeline_manager: 管道管理器实例（可选，用于文本预处理）
    """

    def __init__(
        self,
        event_bus: "EventBus",
        pipeline_manager: Optional["PipelineManager"] = None,
    ):
        """
        初始化CanonicalLayer

        Args:
            event_bus: 事件总线实例
            pipeline_manager: 管道管理器实例（可选）
        """
        self.event_bus = event_bus
        self.pipeline_manager = pipeline_manager
        self.logger = get_logger("CanonicalLayer")

        # 统计信息
        self._received_count = 0
        self._processed_count = 0
        self._dropped_count = 0
        self._error_count = 0

        self.logger.debug("CanonicalLayer初始化完成")

    async def setup(self):
        """设置CanonicalLayer，订阅事件"""
        # 订阅 NormalizedText 就绪事件
        self.event_bus.on("normalization.text.ready", self._on_normalized_text_ready)

        self.logger.info("CanonicalLayer设置完成，已订阅 normalization.text.ready")

    async def cleanup(self):
        """清理CanonicalLayer"""
        # 取消订阅
        self.event_bus.off("normalization.text.ready", self._on_normalized_text_ready)

        self.logger.info(
            f"CanonicalLayer清理完成 "
            f"(received={self._received_count}, processed={self._processed_count}, "
            f"dropped={self._dropped_count}, errors={self._error_count})"
        )

    async def _on_normalized_text_ready(self, event_name: str, event_data: Dict[str, Any], source: str):
        """
        处理 NormalizedText 就绪事件

        Args:
            event_name: 事件名称 ("normalization.text.ready")
            event_data: 事件数据，包含 "normalized" 和 "source"
            source: 事件源
        """
        self._received_count += 1

        try:
            # 提取 NormalizedText
            normalized: Optional[NormalizedText] = event_data.get("normalized")
            original_source = event_data.get("source", source)

            if not normalized:
                self.logger.warning(f"收到空的NormalizedText事件 (source: {source})")
                self._error_count += 1
                return

            self.logger.debug(f"收到NormalizedText: text={normalized.text[:50]}..., source={original_source}")

            # 获取待处理的文本
            text = normalized.text
            metadata = normalized.metadata.copy()

            # 通过 TextPipeline 进行文本预处理（如限流、敏感词过滤等）
            if self.pipeline_manager and hasattr(self.pipeline_manager, "process_text"):
                try:
                    processed_text = await self.pipeline_manager.process_text(text, metadata)
                    if processed_text is None:
                        self.logger.debug(f"文本被 TextPipeline 丢弃 (source: {original_source})")
                        self._dropped_count += 1
                        return
                    text = processed_text
                except Exception as e:
                    # Pipeline 异常时根据配置决定是否继续
                    self.logger.warning(f"TextPipeline 处理异常: {e}，使用原始文本继续")

            # 构建 CanonicalMessage
            canonical = CanonicalMessage.from_normalized_text(normalized)

            # 如果文本被 Pipeline 修改，更新 CanonicalMessage 的文本
            if text != normalized.text:
                canonical.text = text

            # 发布 canonical.message_ready 事件
            await self.event_bus.emit(
                "canonical.message_ready",
                {"canonical": canonical, "source": original_source},
                source="CanonicalLayer",
            )

            self._processed_count += 1

            self.logger.debug(f"生成CanonicalMessage: text={canonical.text[:50]}..., source={canonical.source}")

        except Exception as e:
            self._error_count += 1
            self.logger.error(f"处理NormalizedText事件时出错 (source: {source}): {e}", exc_info=True)

    async def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        return {
            "received_count": self._received_count,
            "processed_count": self._processed_count,
            "dropped_count": self._dropped_count,
            "error_count": self._error_count,
            "success_rate": (self._processed_count / self._received_count if self._received_count > 0 else 0.0),
        }
