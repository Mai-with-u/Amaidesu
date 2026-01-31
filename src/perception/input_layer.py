"""
InputLayer - 输入层协调器

负责协调Layer 1(输入感知)和Layer 2(输入标准化)，建立RawData到NormalizedText的数据流。
"""

from typing import Dict, Any, Optional

from src.core.event_bus import EventBus
from src.core.data_types.raw_data import RawData
from src.core.data_types.normalized_text import NormalizedText
from src.perception.input_provider_manager import InputProviderManager
from src.utils.logger import get_logger


class InputLayer:
    """
    输入层协调器

    负责协调Layer 1(输入感知)和Layer 2(输入标准化)。
    接收RawData事件，转换为NormalizedText，发布到EventBus。
    """

    def __init__(self, event_bus: EventBus, input_provider_manager: Optional[InputProviderManager] = None):
        """
        初始化InputLayer

        Args:
            event_bus: 事件总线实例
            input_provider_manager: InputProviderManager实例（可选，如果不提供则只监听事件）
        """
        self.event_bus = event_bus
        self.input_provider_manager = input_provider_manager
        self.logger = get_logger("InputLayer")

        # 统计信息
        self._raw_data_count = 0
        self._normalized_count = 0

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

            # 转换为NormalizedText
            normalized = await self.normalize(raw_data)

            if normalized:
                self._normalized_count += 1

                # 发布NormalizedText就绪事件
                await self.event_bus.emit(
                    "normalization.text.ready",
                    {"normalized": normalized, "source": raw_data.source},
                    source="InputLayer",
                )

                self.logger.debug(f"生成NormalizedText: text={normalized.text[:50]}..., source={normalized.source}")

        except Exception as e:
            self.logger.error(f"处理RawData事件时出错 (source: {source}): {e}", exc_info=True)

    async def normalize(self, raw_data: RawData) -> Optional[NormalizedText]:
        """
        将RawData转换为NormalizedText

        根据raw_data的data_type选择不同的转换策略:
        - text: 直接转换为文本
        - gift, superchat, guard: 格式化描述为文本
        - audio, image: 简化为文本描述(暂不处理多模态)

        Args:
            raw_data: 原始数据

        Returns:
            NormalizedText对象，转换失败返回None
        """
        try:
            data_type = raw_data.data_type
            content = raw_data.content
            metadata = raw_data.metadata.copy()

            # 添加基本元数据
            metadata["source"] = raw_data.source
            metadata["original_timestamp"] = raw_data.timestamp

            # 根据数据类型转换为文本
            if data_type == "text":
                # 文本直接使用
                text = str(content)

            elif data_type == "gift":
                # 礼物消息格式化
                if isinstance(content, dict):
                    user = content.get("user", "未知用户")
                    gift_name = content.get("gift_name", "未知礼物")
                    count = content.get("count", 1)
                    text = f"{user} 送出了 {count} 个 {gift_name}"
                else:
                    text = f"有人送出了礼物: {str(content)}"

            elif data_type == "superchat":
                # 醒目留言格式化
                if isinstance(content, dict):
                    user = content.get("user", "未知用户")
                    sc_content = content.get("content", "")
                    text = f"{user} 发送了醒目留言: {sc_content}"
                else:
                    text = f"醒目留言: {str(content)}"

            elif data_type == "guard":
                # 大航海格式化
                if isinstance(content, dict):
                    user = content.get("user", "未知用户")
                    level = content.get("level", "大航海")
                    text = f"{user} 开通了{level}"
                else:
                    text = f"有人开通了大航海: {str(content)}"

            else:
                # 未知类型，转换为字符串
                text = f"[{data_type}] {str(content)}"

            # 创建NormalizedText
            normalized = NormalizedText(text=text, metadata=metadata, data_ref=raw_data.data_ref)

            return normalized

        except Exception as e:
            self.logger.error(f"转换RawData为NormalizedText时出错: {e}", exc_info=True)
            return None

    async def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        return {
            "raw_data_count": self._raw_data_count,
            "normalized_count": self._normalized_count,
            "success_rate": (self._normalized_count / self._raw_data_count if self._raw_data_count > 0 else 0.0),
        }
