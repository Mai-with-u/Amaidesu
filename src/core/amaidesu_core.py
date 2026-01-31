"""
Amaidesu Core - 核心模块（Phase 3-4重构版本）

职责: 插件管理、服务注册、Pipeline/Decision/Context/EventBus/HttpServer集成
注意: WebSocket/HTTP/Router已迁移到MaiCoreDecisionProvider（641行→350行）
"""

from typing import Callable, Dict, Any, List, Optional, TYPE_CHECKING

from src.utils.logger import get_logger
from .pipeline_manager import PipelineManager
from .context_manager import ContextManager
from .event_bus import EventBus
from .decision_manager import DecisionManager
from .http_server import HttpServer

# Phase 4: 输出层
from .output_provider_manager import OutputProviderManager
from ..expression.expression_generator import ExpressionGenerator

# LLM 服务（核心基础设施）
from .llm_service import LLMService

# 类型检查时的导入
if TYPE_CHECKING:
    pass


class AmaidesuCore:
    """Amaidesu 核心模块 - 插件管理和服务分发（Phase 3-4重构）"""

    @property
    def event_bus(self) -> Optional[EventBus]:
        """获取事件总线实例"""
        return self._event_bus

    @property
    def avatar(self) -> None:
        """已废弃：AvatarControlManager 已迁移到 Platform Layer"""
        self.logger.warning("AvatarControlManager 已迁移到 Platform Layer，请使用 OutputProvider")
        return None

    @property
    def llm_service(self) -> Optional[LLMService]:
        """获取 LLM 服务实例"""
        return self._llm_service

    @property
    def http_server(self) -> Optional[HttpServer]:
        """获取HTTP服务器实例"""
        return self._http_server

    def __init__(
        self,
        platform: str,
        pipeline_manager: Optional[PipelineManager] = None,
        context_manager: Optional[ContextManager] = None,
        event_bus: Optional[EventBus] = None,
        avatar: Optional["AvatarControlManager"] = None,
        llm_service: Optional[LLMService] = None,
        decision_manager: Optional[DecisionManager] = None,
        output_provider_manager: Optional[OutputProviderManager] = None,
        expression_generator: Optional[ExpressionGenerator] = None,
        http_server: Optional[HttpServer] = None,
    ):
        """
        初始化 Amaidesu Core（重构版本）。

        Args:
            platform: 平台标识符 (例如 "amaidesu_default")。
            pipeline_manager: (可选) 已配置的管道管理器。
            context_manager: (可选) 已配置的上下文管理器。
            event_bus: (可选) 已配置的事件总线。
            avatar: (可选) 已配置的虚拟形象控制管理器。
            llm_service: (可选) 已配置的 LLM 服务。
            decision_manager: (可选) 已配置的决策管理器（Phase 3新增）。
            output_provider_manager: (可选) 已配置的输出Provider管理器（Phase 4新增）。
            expression_generator: (可选) 已配置的表达式生成器（Phase 4新增）。
            http_server: (可选) 已配置的HTTP服务器（Phase 5新增）。
        """
        # 初始化 Logger
        self.logger = get_logger("AmaidesuCore")
        self.logger.debug("AmaidesuCore 初始化开始")

        self.platform = platform

        # HTTP服务器（Phase 5新增）
        self._http_server = http_server
        if http_server is not None:
            self.logger.info("已使用外部提供的HTTP服务器")

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

        # 设置事件总线（可选功能）
        self._event_bus = event_bus
        if event_bus is None:
            self._event_bus = EventBus()
            self.logger.info("创建了默认EventBus实例")
        else:
            self.logger.info("已使用外部提供的事件总线")

        # 设置虚拟形象控制管理器（可选功能，已废弃）
        self._avatar = avatar
        if avatar is not None:
            avatar.core = self
            self.logger.warning("AvatarControlManager 已废弃，请迁移到 Platform Layer")

        # 设置 LLM 服务（可选功能）
        self._llm_service = llm_service
        if llm_service is not None:
            self.logger.info("已使用外部提供的 LLM 服务")
        else:
            self.logger.warning("未提供 LLM 服务，LLM 相关功能将不可用")

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
        # Phase 5: 启动HTTP服务器（如果已配置）
        if self._http_server and self._http_server.is_available:
            try:
                await self._http_server.start()
                self.logger.info("HTTP服务器已启动")

                # 发布 core.ready 事件，让Provider可以注册路由
                if self._event_bus:
                    await self._event_bus.emit(
                        "core.ready",
                        {"core": self},
                        source="AmaidesuCore",
                    )
            except Exception as e:
                self.logger.error(f"启动HTTP服务器失败: {e}", exc_info=True)
                self.logger.warning("HTTP服务器功能不可用，继续启动其他服务")

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

        # Phase 5: 停止HTTP服务器
        if self._http_server and self._http_server.is_running:
            try:
                await self._http_server.stop()
                self.logger.info("HTTP服务器已停止")
            except Exception as e:
                self.logger.error(f"停止HTTP服务器失败: {e}", exc_info=True)

        self.logger.info("核心服务已停止")

    # ==================== HTTP服务器管理（Phase 5新增） ====================

    def register_http_callback(
        self,
        path: str,
        handler: Callable,
        methods: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> bool:
        """
        注册HTTP回调路由（供Provider使用）

        Args:
            path: 路径（如 "/maicore/callback"）
            handler: 处理函数（异步函数）
            methods: 允许的HTTP方法（如 ["GET", "POST"]）
            **kwargs: 传递给 HttpServer.register_route 的其他参数

        Returns:
            是否注册成功
        """
        if not self._http_server:
            self.logger.warning(f"HTTP服务器未初始化，无法注册路由: {path}")
            return False

        return self._http_server.register_route(path, handler, methods, **kwargs)

    def get_context_manager(self) -> ContextManager:
        """
        获取上下文管理器实例。

        Returns:
            上下文管理器实例
        """
        return self._context_manager

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
