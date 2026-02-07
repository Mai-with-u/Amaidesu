"""
FlowCoordinator - 数据流协调器（3域架构：Decision Domain → Output Domain）

职责:
- 协调 Decision Domain 到 Output Domain 的数据流
- 订阅 Intent 事件并触发 Expression 生成和渲染
- 初始化输出层（OutputProviderManager 和 ExpressionGenerator）

数据流（3域架构）:
    Intent (Decision Domain)
        ↓ CoreEvents.DECISION_INTENT_GENERATED
    FlowCoordinator
        ↓
    ExpressionGenerator (Output Domain - Parameters)
        ↓
    OutputProviderManager (Output Domain - Rendering)
"""

from typing import Dict, Any, Optional
from src.core.utils.logger import get_logger

from src.core.event_bus import EventBus
from src.core.events.names import CoreEvents
from src.domains.output.parameters.expression_generator import ExpressionGenerator
from src.domains.output.manager import OutputProviderManager


class FlowCoordinator:
    """
    数据流协调器（3域架构）

    核心职责:
    - 协调 Decision Domain → Output Domain 的数据流
    - 初始化和管理输出层组件
    - 订阅并处理 Intent 事件

    数据流程（3域架构）:
    Intent (Decision) → ExpressionGenerator (Output - Parameters) → OutputProviderManager (Output - Rendering)
    """

    def __init__(
        self,
        event_bus: EventBus,
        expression_generator: Optional[ExpressionGenerator] = None,
        output_provider_manager: Optional[OutputProviderManager] = None,
    ):
        """
        初始化数据流协调器

        Args:
            event_bus: 事件总线实例
            expression_generator: (可选) 表达式生成器实例
            output_provider_manager: (可选) 输出Provider管理器实例
        """
        self.event_bus = event_bus
        self.expression_generator = expression_generator
        self.output_provider_manager = output_provider_manager
        self.logger = get_logger("FlowCoordinator")

        self._is_setup = False
        self._event_handler_registered = False

        self.logger.info("FlowCoordinator 初始化完成")

    async def setup(self, config: Dict[str, Any], config_service=None):
        """
        设置数据流协调器

        Args:
            config: 输出Provider配置（来自[providers.output]）
            config_service: ConfigService实例（用于三级配置加载）
        """
        self.logger.info("开始设置数据流协调器...")

        # 创建表达式生成器（如果未提供）
        if self.expression_generator is None:
            expression_config = config.get("expression_generator", {})
            self.expression_generator = ExpressionGenerator(expression_config)
            self.logger.info("表达式生成器已创建")

        # 创建输出Provider管理器（如果未提供）
        if self.output_provider_manager is None:
            self.output_provider_manager = OutputProviderManager(config)
            self.logger.info("输出Provider管理器已创建")

        # 从配置加载Provider（传递config_service以启用三级配置合并）
        if self.output_provider_manager:
            await self.output_provider_manager.load_from_config(config, core=None, config_service=config_service)

        # 订阅 Decision Domain 的 Intent 事件（3域架构）
        self.event_bus.on(CoreEvents.DECISION_INTENT_GENERATED, self._on_intent_ready, priority=50)
        self._event_handler_registered = True
        self.logger.info(f"已订阅 '{CoreEvents.DECISION_INTENT_GENERATED}' 事件")

        self._is_setup = True
        self.logger.info("数据流协调器设置完成")

    async def start(self):
        """启动数据流协调器"""
        if not self._is_setup:
            self.logger.warning("FlowCoordinator 未设置，无法启动")
            return

        self.logger.info("启动数据流协调器...")

        # 启动OutputProvider
        if self.output_provider_manager:
            try:
                await self.output_provider_manager.setup_all_providers(self.event_bus)
                self.logger.info("OutputProvider 已启动")
            except Exception as e:
                self.logger.error(f"启动 OutputProvider 失败: {e}", exc_info=True)

    async def stop(self):
        """停止数据流协调器"""
        self.logger.info("停止数据流协调器...")

        # 停止OutputProvider
        if self.output_provider_manager:
            try:
                await self.output_provider_manager.stop_all_providers()
                self.logger.info("OutputProvider 已停止")
            except Exception as e:
                self.logger.error(f"停止 OutputProvider 失败: {e}", exc_info=True)

    async def cleanup(self):
        """清理资源"""
        self.logger.info("清理数据流协调器...")

        # 取消事件订阅
        if self._event_handler_registered:
            try:
                self.event_bus.off(CoreEvents.DECISION_INTENT_GENERATED, self._on_intent_ready)
                self._event_handler_registered = False
                self.logger.info("事件订阅已取消")
            except Exception as e:
                self.logger.error(f"取消事件订阅失败: {e}", exc_info=True)

        self._is_setup = False
        self.logger.info("数据流协调器清理完成")

    async def _on_intent_ready(self, event_name: str, event_data: Dict[str, Any], source: str):
        """
        处理Intent事件（Decision Domain → Output Domain）

        数据流（事件驱动）:
            IntentPayload → Intent → ExpressionParameters → 发布 expression.parameters_generated 事件 → OutputProvider 订阅并渲染

        Args:
            event_name: 事件名称（decision.intent_generated）
            event_data: 事件数据（IntentPayload 格式）
            source: 事件源
        """
        self.logger.info(f"收到Intent事件: {event_name}")

        try:
            # 从 IntentPayload 重建 Intent 对象
            from src.domains.decision.intent import Intent, IntentAction, EmotionType

            # 提取 IntentPayload 的字段
            original_text = event_data.get("original_text", "")
            response_text = event_data.get("response_text", "")
            emotion_str = event_data.get("emotion", "neutral")
            actions_data = event_data.get("actions", [])
            metadata = event_data.get("metadata", {})
            timestamp = event_data.get("timestamp", 0)

            # 转换 emotion 字符串为 EmotionType
            try:
                emotion = EmotionType(emotion_str)
            except ValueError:
                emotion = EmotionType.NEUTRAL

            # 转换 actions 数据为 IntentAction 对象
            actions = []
            for action_data in actions_data:
                try:
                    from src.domains.decision.intent import ActionType
                    action = IntentAction(
                        type=ActionType(action_data.get("type", "text")),
                        params=action_data.get("params", {}),
                        priority=action_data.get("priority", 100),
                    )
                    actions.append(action)
                except Exception as e:
                    self.logger.warning(f"跳过无效的 IntentAction: {e}")

            # 重建 Intent 对象
            intent = Intent(
                original_text=original_text,
                response_text=response_text,
                emotion=emotion,
                actions=actions,
                metadata=metadata,
                timestamp=timestamp,
            )

            # Output Domain - Parameters: Intent → ExpressionParameters
            if self.expression_generator:
                params = await self.expression_generator.generate(intent)
                self.logger.info("ExpressionParameters生成完成")

                # 发布 expression.parameters_generated 事件（事件驱动）
                # OutputProvider 订阅此事件并响应
                await self.event_bus.emit(CoreEvents.EXPRESSION_PARAMETERS_GENERATED, params, source="FlowCoordinator")
                self.logger.debug(f"已发布事件: {CoreEvents.EXPRESSION_PARAMETERS_GENERATED}")
            else:
                self.logger.warning("表达式生成器未初始化，跳过渲染")

        except Exception as e:
            self.logger.error(f"处理Intent事件时出错: {e}", exc_info=True)

    def get_expression_generator(self) -> Optional[ExpressionGenerator]:
        """获取表达式生成器实例"""
        return self.expression_generator

    def get_output_provider_manager(self) -> Optional[OutputProviderManager]:
        """获取输出Provider管理器实例"""
        return self.output_provider_manager
