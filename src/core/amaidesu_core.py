"""
Amaidesu Core - 核心模块（Phase 3-4重构版本）

职责: 插件管理、服务注册、Pipeline/Decision/Context/EventBus集成
注意: WebSocket/HTTP/Router已迁移到MaiCoreDecisionProvider（641行→350行）
"""

from typing import Dict, Any, Optional, TYPE_CHECKING

from src.utils.logger import get_logger
from .pipeline_manager import PipelineManager
from .context_manager import ContextManager
from .event_bus import EventBus
from .decision_manager import DecisionManager

# Phase 4: 输出层
from .output_provider_manager import OutputProviderManager
from ..expression.expression_generator import ExpressionGenerator

# 类型检查时的导入
if TYPE_CHECKING:
    from .avatar.avatar_manager import AvatarControlManager
    from .llm_client_manager import LLMClientManager


class AmaidesuCore:
    """Amaidesu 核心模块 - 插件管理和服务分发（Phase 3-4重构）"""

    @property
    def event_bus(self) -> Optional[EventBus]:
        """获取事件总线实例"""
        return self._event_bus

    @property
    def avatar(self) -> Optional["AvatarControlManager"]:
        """获取虚拟形象控制管理器实例"""
        return self._avatar

    @property
    def llm_client_manager(self) -> Optional["LLMClientManager"]:
        """获取LLM客户端管理器实例"""
        return self._llm_client_manager

    def __init__(
        self,
        platform: str,
        pipeline_manager: Optional[PipelineManager] = None,
        context_manager: Optional[ContextManager] = None,
        event_bus: Optional[EventBus] = None,
        avatar: Optional["AvatarControlManager"] = None,
        llm_client_manager: Optional["LLMClientManager"] = None,
        decision_manager: Optional[DecisionManager] = None,
        output_provider_manager: Optional[OutputProviderManager] = None,
        expression_generator: Optional[ExpressionGenerator] = None,
    ):
        """
        初始化 Amaidesu Core（重构版本）。

        Args:
            platform: 平台标识符 (例如 "amaidesu_default")。
            pipeline_manager: (可选) 已配置的管道管理器。
            context_manager: (可选) 已配置的上下文管理器。
            event_bus: (可选) 已配置的事件总线。
            avatar: (可选) 已配置的虚拟形象控制管理器。
            llm_client_manager: (可选) 已配置的 LLM 客户端管理器。
            decision_manager: (可选) 已配置的决策管理器（Phase 3新增）。
            output_provider_manager: (可选) 已配置的输出Provider管理器（Phase 4新增）。
            expression_generator: (可选) 已配置的表达式生成器（Phase 4新增）。
        """
        # 初始化 Logger
        self.logger = get_logger("AmaidesuCore")
        self.logger.debug("AmaidesuCore 初始化开始")

        self.platform = platform

        # 服务注册表
        self._services: Dict[str, Any] = {}

        # 管道管理器
        self._pipeline_manager = pipeline_manager
        if self._pipeline_manager is None:
            self.logger.info("管道处理功能已禁用")
        else:
            self.logger.info("管道处理功能已启用")
            self._pipeline_manager.core = self
            self._pipeline_manager._set_core_for_all_pipelines()

        # 设置上下文管理器
        self._context_manager = context_manager if context_manager is not None else ContextManager({})
        self.register_service("prompt_context", self._context_manager)
        self.logger.info("上下文管理器已注册为服务")

        # 设置事件总线（可选功能）
        self._event_bus = event_bus
        if event_bus is None:
            self._event_bus = EventBus()
            self.logger.info("创建了默认EventBus实例")
        else:
            self.logger.info("已使用外部提供的事件总线")

        # 设置虚拟形象控制管理器（可选功能）
        self._avatar = avatar
        if avatar is not None:
            avatar.core = self
            self.logger.info("已使用外部提供的虚拟形象控制管理器")

        # 设置 LLM 客户端管理器（可选功能）
        self._llm_client_manager = llm_client_manager
        if llm_client_manager is not None:
            self.logger.info("已使用外部提供的 LLM 客户端管理器")
        else:
            self.logger.warning("未提供 LLM 客户端管理器，LLM 相关功能将不可用")

        # 设置决策管理器（Phase 3新增）
        self._decision_manager = decision_manager
        if decision_manager is not None:
            self.logger.info("已使用外部提供的决策管理器")

        # 设置输出Provider管理器（Phase 4新增）
        self._output_provider_manager = output_provider_manager
        if output_provider_manager is not None:
            self.logger.info("已使用外部提供的输出Provider管理器")

        # 设置表达式生成器（Phase 4新增）
        self._expression_generator = expression_generator
        if expression_generator is not None:
            self.logger.info("已使用外部提供的表达式生成器")

        self.logger.debug("AmaidesuCore 初始化完成")

    async def connect(self, rendering_config: Optional[Dict[str, Any]] = None):
        """
        启动核心服务

        Args:
            rendering_config: (可选) 渲染层配置，用于设置输出层
        """
        # 如果有决策管理器，启动DecisionProvider
        if self._decision_manager:
            provider = self._decision_manager.get_current_provider()
            if hasattr(provider, "connect"):
                try:
                    await provider.connect()
                    self.logger.info("DecisionProvider 连接已启动")
                except Exception as e:
                    self.logger.error(f"DecisionProvider 连接失败: {e}", exc_info=True)

        # Phase 4: 设置并启动输出层
        if rendering_config:
            try:
                await self._setup_output_layer(rendering_config)
            except Exception as e:
                self.logger.error(f"设置输出层失败: {e}", exc_info=True)
                self.logger.warning("输出层功能可能不可用，继续启动其他服务")

        # Phase 4: 启动OutputProvider（如果已经通过_setup_output_layer创建了）
        if self._output_provider_manager:
            try:
                await self._output_provider_manager.setup_all_providers(self._event_bus)
                self.logger.info("OutputProvider 已启动")
            except Exception as e:
                self.logger.error(f"启动 OutputProvider 失败: {e}", exc_info=True)

    async def disconnect(self):
        """停止核心服务"""
        # Phase 4: 停止OutputProvider
        if self._output_provider_manager:
            try:
                await self._output_provider_manager.stop_all_providers()
                self.logger.info("OutputProvider 已停止")
            except Exception as e:
                self.logger.error(f"停止 OutputProvider 失败: {e}", exc_info=True)

        # 如果有决策管理器，断开DecisionProvider
        if self._decision_manager:
            provider = self._decision_manager.get_current_provider()
            if hasattr(provider, "disconnect"):
                try:
                    await provider.disconnect()
                    self.logger.info("DecisionProvider 连接已断开")
                except Exception as e:
                    self.logger.error(f"DecisionProvider 断开失败: {e}", exc_info=True)

        self.logger.info("核心服务已停止")

    # ==================== 服务注册与发现 ====================

    def register_service(self, name: str, service_instance: Any):
        """
        注册一个服务实例，供其他插件或模块使用。

        Args:
            name: 服务的唯一名称 (例如 "text_cleanup", "vts_control")。
            service_instance: 提供服务的对象实例。
        """
        if name in self._services:
            self.logger.warning(f"服务名称 '{name}' 已被注册，将被覆盖！")
        self._services[name] = service_instance
        self.logger.info(f"服务已注册: '{name}' (类型: {type(service_instance).__name__})")

    def get_service(self, name: str) -> Optional[Any]:
        """
        根据名称获取已注册的服务实例。

        Args:
            name: 要获取的服务名称。

        Returns:
            服务实例，如果找到的话；否则返回 None。
        """
        service = self._services.get(name)
        if service:
            self.logger.debug(f"获取服务 '{name}' 成功。")
        else:
            self.logger.warning(f"尝试获取未注册的服务: '{name}'")
        return service

    def get_context_manager(self) -> ContextManager:
        """
        获取上下文管理器实例。

        Returns:
            上下文管理器实例
        """
        return self._context_manager

    # ==================== LLM 客户端管理 ====================

    def get_llm_client(self, config_type: str = "llm"):
        """
        获取 LLM 客户端实例（委托给 LLMClientManager）

        Args:
            config_type: 配置类型，可选值：
                - "llm": 标准 LLM 配置（默认）
                - "llm_fast": 快速 LLM 配置（低延迟场景）
                - "vlm": 视觉语言模型配置

        Returns:
            LLMClient 实例

        Raises:
            ValueError: 如果 LLMClientManager 未提供或配置无效
        """

        if self._llm_client_manager is None:
            raise ValueError("LLM 客户端管理器未初始化！请在 main.py 中创建 LLMClientManager 并传入 AmaidesuCore。")

        return self._llm_client_manager.get_client(config_type)

    # ==================== 决策管理器（Phase 3新增） ====================

    @property
    def decision_manager(self) -> Optional[DecisionManager]:
        """获取决策管理器实例"""
        return self._decision_manager

    def set_decision_manager(self, decision_manager: DecisionManager):
        """
        设置决策管理器

        Args:
            decision_manager: DecisionManager实例
        """
        self._decision_manager = decision_manager
        self.logger.info("决策管理器已设置")

    # ==================== 输出层管理器（Phase 4新增） ====================

    @property
    def output_provider_manager(self) -> Optional[OutputProviderManager]:
        """获取输出Provider管理器实例"""
        return self._output_provider_manager

    @property
    def expression_generator(self) -> Optional[ExpressionGenerator]:
        """获取表达式生成器实例"""
        return self._expression_generator

    async def _setup_output_layer(self, config: Dict[str, Any]):
        """
        设置输出层（Phase 4新增）

        Args:
            config: 渲染配置（来自[rendering]）
        """
        self.logger.info("开始设置输出层...")

        # 创建表达式生成器（如果未提供）
        if self._expression_generator is None:
            expression_config = config.get("expression_generator", {})
            self._expression_generator = ExpressionGenerator(expression_config)
            self.logger.info("表达式生成器已创建")

        # 创建输出Provider管理器（如果未提供）
        if self._output_provider_manager is None:
            self._output_provider_manager = OutputProviderManager(config)
            self.logger.info("输出Provider管理器已创建")

        # 从配置加载Provider
        if self._output_provider_manager:
            await self._output_provider_manager.load_from_config(config, core=self)

        # 订阅Layer 4的Intent事件
        if self._event_bus:
            self._event_bus.on("understanding.intent_generated", self._on_intent_ready, priority=50)
            self.logger.info("已订阅 'understanding.intent_generated' 事件")

        self.logger.info("输出层设置完成")

    async def _on_intent_ready(self, event_name: str, event_data: Dict[str, Any], source: str):
        """
        处理Intent事件（Layer 4 → Layer 5 → Layer 6）（Phase 4新增）

        Args:
            event_name: 事件名称
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

            # Layer 5: Intent → ExpressionParameters
            if self._expression_generator:
                params = await self._expression_generator.generate(intent)
                self.logger.info(f"ExpressionParameters生成完成: {params}")

                # Layer 6: ExpressionParameters → OutputProvider
                if self._output_provider_manager:
                    await self._output_provider_manager.render_all(params)
            else:
                self.logger.warning("表达式生成器未初始化，跳过渲染")

        except Exception as e:
            self.logger.error(f"处理Intent事件时出错: {e}", exc_info=True)
