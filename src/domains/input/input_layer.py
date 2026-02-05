"""
InputLayer - 输入层协调器

负责协调Layer 1(输入感知)和Layer 2(输入标准化)，建立RawData到NormalizedMessage的数据流。
"""

from typing import Dict, Any, Optional, TYPE_CHECKING

from src.core.event_bus import EventBus
from src.core.base.raw_data import RawData
from src.core.base.normalized_message import NormalizedMessage
from src.domains.input.input_provider_manager import InputProviderManager
from src.core.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.pipeline.pipeline_manager import PipelineManager


class InputLayer:
    """
    输入层协调器（5层架构：Layer 1-2）

    负责协调Layer 1(输入感知)和Layer 2(输入标准化)。
    接收RawData事件，转换为NormalizedMessage，发布到EventBus。

    Pipeline 集成：
        - 在 normalize() 方法中使用 TextPipeline 进行文本预处理
        - PipelineManager 由外部注入（可选）
    """

    def __init__(
        self,
        event_bus: EventBus,
        input_provider_manager: Optional[InputProviderManager] = None,
        pipeline_manager: Optional["PipelineManager"] = None,
    ):
        """
        初始化InputLayer

        Args:
            event_bus: 事件总线实例
            input_provider_manager: InputProviderManager实例（可选，如果不提供则只监听事件）
            pipeline_manager: PipelineManager实例（可选，用于文本预处理）
        """
        self.event_bus = event_bus
        self.input_provider_manager = input_provider_manager
        self.pipeline_manager = pipeline_manager
        self.logger = get_logger("InputLayer")

        # 统计信息
        self._raw_data_count = 0
        self._normalized_message_count = 0

        self.logger.debug("InputLayer初始化完成")

    async def setup(self):
        """设置InputLayer，订阅事件"""
        # 订阅RawData生成事件
        self.event_bus.on("perception.raw_data.generated", self.on_raw_data_generated)

        self.logger.info("InputLayer设置完成")

    async def cleanup(self):
        """清理InputLayer"""
        # 取消订阅
        self.event_bus.off("perception.raw_data.generated", self.on_raw_data_generated)

        self.logger.info("InputLayer清理完成")

    async def on_raw_data_generated(self, event_name: str, event_data: Dict[str, Any], source: str):
        """
        处理RawData生成事件

        Args:
            event_name: 事件名称("perception.raw_data.generated")
            event_data: 事件数据，包含"data"和"source"
            source: 事件源
        """
        try:
            raw_data = event_data.get("data")
            if not raw_data:
                self.logger.warning(f"收到空的RawData事件 (source: {source})")
                return

            self._raw_data_count += 1

            self.logger.debug(
                f"收到RawData: source={raw_data.source}, "
                f"type={raw_data.data_type}, "
                f"content={str(raw_data.content)[:50]}..."
            )

            # 转换为NormalizedMessage
            normalized_message = await self.normalize(raw_data)

            if normalized_message:
                self._normalized_message_count += 1

                # 发布NormalizedMessage就绪事件
                await self.event_bus.emit(
                    "normalization.message_ready",
                    {"message": normalized_message, "source": raw_data.source},
                    source="InputLayer",
                )

                self.logger.debug(
                    f"生成NormalizedMessage: text={normalized_message.text[:50]}..., "
                    f"source={normalized_message.source}, "
                    f"importance={normalized_message.importance:.2f}"
                )

        except Exception as e:
            self.logger.error(f"处理RawData事件时出错 (source: {source}): {e}", exc_info=True)

    async def normalize(self, raw_data: RawData) -> Optional[NormalizedMessage]:
        """
        将RawData转换为NormalizedMessage

        使用NormalizerRegistry根据data_type选择对应的Normalizer:
        - gift: GiftNormalizer
        - superchat: SuperChatNormalizer
        - guard: GuardNormalizer
        - text: TextNormalizer
        - 其他: 降级为文本

        Args:
            raw_data: 原始数据

        Returns:
            NormalizedMessage对象，转换失败返回None
        """
        try:
            from src.domains.input.normalization.normalizers import NormalizerRegistry
            from src.domains.input.normalization.content import TextContent

            # 查找合适的 Normalizer
            normalizer = NormalizerRegistry.get_normalizer(raw_data.data_type)

            if normalizer:
                # 对于 TextNormalizer，需要传递 pipeline_manager
                if hasattr(normalizer, 'pipeline_manager'):
                    normalizer.pipeline_manager = self.pipeline_manager

                return await normalizer.normalize(raw_data)

            # 降级处理：未知类型转换为文本
            text = f"[{raw_data.data_type}] {str(raw_data.content)}"
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
        except Exception as e:
            self.logger.error(f"转换RawData为NormalizedMessage时出错: {e}", exc_info=True)
            return None

    async def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        return {
            "raw_data_count": self._raw_data_count,
            "normalized_message_count": self._normalized_message_count,
            "success_rate": (
                self._normalized_message_count / self._raw_data_count
                if self._raw_data_count > 0
                else 0.0
            ),
        }
