"""
UnderstandingLayer - Layer 5: 表现理解层

负责将决策响应 (MessageBase) 解析为意图 (Intent)。
订阅 decision.response_generated 事件，发布 understanding.intent_generated 事件。

数据流:
    DecisionProvider → decision.response_generated → UnderstandingLayer → understanding.intent_generated → FlowCoordinator
"""

from typing import Dict, Any, TYPE_CHECKING

from src.utils.logger import get_logger
from src.layers.understanding.response_parser import ResponseParser

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from maim_message import MessageBase


class UnderstandingLayer:
    """
    Layer 5: 表现理解层

    职责:
    - 订阅 decision.response_generated 事件
    - 使用 ResponseParser 将 MessageBase 解析为 Intent
    - 发布 understanding.intent_generated 事件

    Attributes:
        event_bus: 事件总线实例
        response_parser: 响应解析器实例
    """

    def __init__(self, event_bus: "EventBus"):
        """
        初始化 UnderstandingLayer

        Args:
            event_bus: 事件总线实例
        """
        self.event_bus = event_bus
        self.response_parser = ResponseParser()
        self.logger = get_logger("UnderstandingLayer")

        # 统计信息
        self._received_count = 0
        self._parsed_count = 0
        self._error_count = 0

        self.logger.debug("UnderstandingLayer 初始化完成")

    async def setup(self):
        """设置 UnderstandingLayer，订阅事件"""
        # 订阅决策响应生成事件
        self.event_bus.on("decision.response_generated", self._on_response_generated)

        self.logger.info("UnderstandingLayer 设置完成，已订阅 decision.response_generated")

    async def cleanup(self):
        """清理 UnderstandingLayer"""
        # 取消订阅
        self.event_bus.off("decision.response_generated", self._on_response_generated)

        self.logger.info(
            f"UnderstandingLayer 清理完成 "
            f"(received={self._received_count}, parsed={self._parsed_count}, errors={self._error_count})"
        )

    async def _on_response_generated(self, event_name: str, event_data: Dict[str, Any], source: str):
        """
        处理决策响应生成事件

        Args:
            event_name: 事件名称 ("decision.response_generated")
            event_data: 事件数据，包含 "message" (MessageBase)
            source: 事件源
        """
        self._received_count += 1

        try:
            # 提取 MessageBase
            message: "MessageBase" = event_data.get("message")

            if not message:
                self.logger.warning(f"收到空的 MessageBase 事件 (source: {source})")
                self._error_count += 1
                return

            self.logger.debug(f"收到决策响应: message_id={message.message_info.message_id if message.message_info else 'unknown'}")

            # 使用 ResponseParser 解析 MessageBase 为 Intent
            intent = self.response_parser.parse(message)

            # 发布 understanding.intent_generated 事件
            await self.event_bus.emit(
                "understanding.intent_generated",
                {"intent": intent, "original_message": message},
                source="UnderstandingLayer",
            )

            self._parsed_count += 1

            self.logger.debug(f"生成 Intent: text={intent.original_text[:50] if intent.original_text else 'empty'}..., emotion={intent.emotion}")

        except Exception as e:
            self._error_count += 1
            self.logger.error(f"处理决策响应事件时出错 (source: {source}): {e}", exc_info=True)

    async def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        return {
            "received_count": self._received_count,
            "parsed_count": self._parsed_count,
            "error_count": self._error_count,
            "success_rate": (self._parsed_count / self._received_count if self._received_count > 0 else 0.0),
        }
