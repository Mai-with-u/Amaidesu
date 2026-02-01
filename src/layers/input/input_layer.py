"""
InputLayer - 输入层协调器

负责协调Layer 1(输入感知)和Layer 2(输入标准化)，建立RawData到NormalizedMessage的数据流。
"""

from typing import Dict, Any, Optional, TYPE_CHECKING

from src.core.event_bus import EventBus
from src.data_types.raw_data import RawData
from src.data_types.normalized_message import NormalizedMessage
from src.layers.input.input_provider_manager import InputProviderManager
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.layers.normalization.content import StructuredContent


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

        根据raw_data的data_type选择不同的转换策略:
        - text: 创建TextContent
        - gift: 创建GiftContent
        - superchat: 创建SuperChatContent
        - guard: 创建TextContent(带有特殊标记)
        - audio, image: 简化为文本描述(暂不处理多模态)

        Args:
            raw_data: 原始数据

        Returns:
            NormalizedMessage对象，转换失败返回None
        """
        try:
            data_type = raw_data.data_type
            content = raw_data.content
            metadata = raw_data.metadata.copy()

            # 添加基本元数据
            metadata["source"] = raw_data.source
            metadata["original_timestamp"] = raw_data.timestamp

            # 导入StructuredContent类型
            from src.layers.normalization.content import (
                TextContent,
                GiftContent,
                SuperChatContent,
            )

            # 根据数据类型创建对应的StructuredContent
            structured_content: "StructuredContent"

            if data_type == "gift":
                # 礼物消息
                if isinstance(content, dict):
                    user = content.get("user", "未知用户")
                    gift_name = content.get("gift_name", "未知礼物")
                    gift_level = content.get("gift_level", 1)
                    count = content.get("count", 1)
                    value = content.get("value", 0.0)

                    structured_content = GiftContent(
                        user=user,
                        gift_name=gift_name,
                        gift_level=gift_level,
                        count=count,
                        value=value,
                    )
                else:
                    # 降级为文本
                    structured_content = TextContent(text=f"有人送出了礼物: {str(content)}")

            elif data_type == "superchat":
                # 醒目留言
                if isinstance(content, dict):
                    user = content.get("user", "未知用户")
                    sc_content = content.get("content", "")
                    price = content.get("price", 0.0)

                    structured_content = SuperChatContent(
                        user=user,
                        content=sc_content,
                        price=price,
                    )
                else:
                    structured_content = TextContent(text=f"醒目留言: {str(content)}")

            elif data_type == "guard":
                # 大航海
                if isinstance(content, dict):
                    user = content.get("user", "未知用户")
                    level = content.get("level", "大航海")
                    text = f"{user} 开通了{level}"
                else:
                    text = f"有人开通了大航海: {str(content)}"
                structured_content = TextContent(text=text)

            elif data_type == "text":
                # 文本类型：应用 TextPipeline 预处理
                text = str(content)

                # 如果配置了 PipelineManager，使用 TextPipeline 处理文本
                if self.pipeline_manager:
                    try:
                        processed_text = await self.pipeline_manager.process_text(text, metadata)
                        if processed_text is None:
                            # Pipeline 返回 None 表示丢弃该消息
                            self.logger.debug(f"文本被Pipeline丢弃: {text[:50]}...")
                            return None
                        text = processed_text
                    except Exception as e:
                        self.logger.error(f"TextPipeline处理失败: {e}", exc_info=True)
                        # 出错时使用原文本，不中断流程

                structured_content = TextContent(text=text)

            else:
                # 未知类型，转换为文本
                text = f"[{data_type}] {str(content)}"
                structured_content = TextContent(text=text)

            # 构建NormalizedMessage
            return NormalizedMessage(
                text=structured_content.get_display_text(),
                content=structured_content,
                source=raw_data.source,
                data_type=data_type,
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
