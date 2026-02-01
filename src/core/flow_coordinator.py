"""
FlowCoordinator - 数据流协调器（5层架构：Layer 3 → Layer 4-5）

职责:
- 协调 Layer 3 (Decision) 到 Layer 4-5 (Parameters+Rendering) 的数据流
- 订阅 Intent 事件并触发 Expression 生成和渲染
- 初始化输出层（OutputProviderManager 和 ExpressionGenerator）

数据流（5层架构）:
    Intent (Layer 3 Decision)
        ↓ decision.intent_generated
    FlowCoordinator
        ↓
    ExpressionGenerator (Layer 4 Parameters)
        ↓
    OutputProviderManager (Layer 5 Rendering)
"""

from typing import Dict, Any, Optional
from src.utils.logger import get_logger

from .event_bus import EventBus
from .events.names import CoreEvents
from src.layers.parameters.expression_generator import ExpressionGenerator
from .output_provider_manager import OutputProviderManager


class FlowCoordinator:
    """
    数据流协调器（5层架构）

    核心职责:
    - 协调 Layer 3 (Decision) → Layer 4-5 (Parameters+Rendering) 的数据流
    - 初始化和管理输出层组件
    - 订阅并处理 Intent 事件

    数据流程（5层架构）:
    Intent (Layer 3) → ExpressionGenerator (Layer 4) → OutputProviderManager (Layer 5)
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

    async def setup(self, config: Dict[str, Any]):
        """
        设置数据流协调器

        Args:
            config: 渲染配置（来自[rendering]）
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

        # 从配置加载Provider
        if self.output_provider_manager:
            await self.output_provider_manager.load_from_config(config, core=None)

        # 订阅 Layer 3 (Decision) 的 Intent 事件（5层架构）
        self.event_bus.on("decision.intent_generated", self._on_intent_ready, priority=50)
        self._event_handler_registered = True
        self.logger.info("已订阅 'decision.intent_generated' 事件")

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
                self.event_bus.off("decision.intent_generated", self._on_intent_ready)
                self._event_handler_registered = False
                self.logger.info("事件订阅已取消")
            except Exception as e:
                self.logger.error(f"取消事件订阅失败: {e}", exc_info=True)

        self._is_setup = False
        self.logger.info("数据流协调器清理完成")

    async def _on_intent_ready(self, event_name: str, event_data: Dict[str, Any], source: str):
        """
        处理Intent事件（Layer 3 Decision → Layer 4-5 Parameters+Rendering）

        数据流（事件驱动）:
            Intent → ExpressionParameters → 发布 expression.parameters_generated 事件 → OutputProvider 订阅并渲染

        Args:
            event_name: 事件名称（decision.intent_generated）
            event_data: 事件数据（包含intent对象）
            source: 事件源
        """
        self.logger.info(f"收到Intent事件: {event_name}")

        try:
            # 提取Intent对象
            intent = event_data.get("intent")
            if not intent:
                self.logger.error("事件数据中缺少intent对象")
                return

            # Layer 4: Intent → ExpressionParameters
            if self.expression_generator:
                params = await self.expression_generator.generate(intent)
                self.logger.info(f"ExpressionParameters生成完成")

                # Layer 5: 发布 expression.parameters_generated 事件（事件驱动）
                # OutputProvider 订阅此事件并响应
                await self.event_bus.emit(
                    CoreEvents.EXPRESSION_PARAMETERS_GENERATED,
                    params,
                    source="FlowCoordinator"
                )
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
