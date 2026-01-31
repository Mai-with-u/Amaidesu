"""
Amaidesu Core - 核心模块（Phase 3-4重构版本）

职责: 组件组合（Composition Root）
注意: 数据流处理已迁移到FlowCoordinator（A-01重构完成）
"""

from typing import Optional, TYPE_CHECKING

from src.utils.logger import get_logger
from .pipeline_manager import PipelineManager
from .context_manager import ContextManager
from .event_bus import EventBus
from .decision_manager import DecisionManager
from .http_server import HttpServer
from .flow_coordinator import FlowCoordinator

# LLM 服务（核心基础设施）
from .llm_service import LLMService

# 类型检查时的导入
if TYPE_CHECKING:
    pass


class AmaidesuCore:
    """Amaidesu 核心模块 - 组件组合根（Composition Root）"""

    @property
    def event_bus(self) -> Optional[EventBus]:
        """获取事件总线实例"""
        return self._event_bus

    @property
    def llm_service(self) -> Optional[LLMService]:
        """获取 LLM 服务实例"""
        return self._llm_service

    @property
    def http_server(self) -> Optional[HttpServer]:
        """获取HTTP服务器实例"""
        return self._http_server

    @property
    def flow_coordinator(self) -> Optional[FlowCoordinator]:
        """获取数据流协调器实例"""
        return self._flow_coordinator

    def __init__(
        self,
        platform: str,
        pipeline_manager: Optional[PipelineManager] = None,
        context_manager: Optional[ContextManager] = None,
        event_bus: Optional[EventBus] = None,
        llm_service: Optional[LLMService] = None,
        decision_manager: Optional[DecisionManager] = None,
        flow_coordinator: Optional[FlowCoordinator] = None,
        http_server: Optional[HttpServer] = None,
    ):
        """
        初始化 Amaidesu Core（A-01重构版本 - 纯组合根）。

        Args:
            platform: 平台标识符 (例如 "amaidesu_default")。
            pipeline_manager: (可选) 已配置的管道管理器。
            context_manager: (可选) 已配置的上下文管理器。
            event_bus: (可选) 已配置的事件总线。
            llm_service: (可选) 已配置的 LLM 服务。
            decision_manager: (可选) 已配置的决策管理器。
            flow_coordinator: (可选) 已配置的数据流协调器（A-01新增）。
            http_server: (可选) 已配置的HTTP服务器。
        """
        self.logger = get_logger("AmaidesuCore")
        self.logger.debug("AmaidesuCore 初始化开始")

        self.platform = platform

        # HTTP服务器
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

        # 设置事件总线
        self._event_bus = event_bus
        if event_bus is None:
            self._event_bus = EventBus()
            self.logger.info("创建了默认EventBus实例")
        else:
            self.logger.info("已使用外部提供的事件总线")

        # 设置 LLM 服务
        self._llm_service = llm_service
        if llm_service is not None:
            self.logger.info("已使用外部提供的 LLM 服务")
        else:
            self.logger.warning("未提供 LLM 服务，LLM 相关功能将不可用")

        # 设置决策管理器
        self._decision_manager = decision_manager
        if decision_manager is not None:
            self.logger.info("已使用外部提供的决策管理器")

        # 设置数据流协调器（A-01新增）
        self._flow_coordinator = flow_coordinator
        if flow_coordinator is not None:
            self.logger.info("已使用外部提供的数据流协调器")

        self.logger.debug("AmaidesuCore 初始化完成")

    async def connect(self):
        """启动核心服务"""
        # 启动HTTP服务器（如果已配置）
        if self._http_server and self._http_server.is_available:
            try:
                await self._http_server.start()
                self.logger.info("HTTP服务器已启动")

                # 发布 core.ready 事件
                if self._event_bus:
                    await self._event_bus.emit(
                        "core.ready",
                        {"core": self},
                        source="AmaidesuCore",
                    )
            except Exception as e:
                self.logger.error(f"启动HTTP服务器失败: {e}", exc_info=True)
                self.logger.warning("HTTP服务器功能不可用，继续启动其他服务")

        # 启动DecisionProvider（如果已配置）
        if self._decision_manager:
            provider = self._decision_manager.get_current_provider()
            if hasattr(provider, "connect"):
                try:
                    await provider.connect()
                    self.logger.info("DecisionProvider 连接已启动")
                except Exception as e:
                    self.logger.error(f"DecisionProvider 连接失败: {e}", exc_info=True)

        # 启动数据流协调器（A-01新增）
        if self._flow_coordinator:
            try:
                await self._flow_coordinator.start()
                self.logger.info("数据流协调器已启动")
            except Exception as e:
                self.logger.error(f"启动数据流协调器失败: {e}", exc_info=True)

    async def disconnect(self):
        """停止核心服务"""
        # 停止数据流协调器（A-01新增）
        if self._flow_coordinator:
            try:
                await self._flow_coordinator.stop()
                self.logger.info("数据流协调器已停止")
            except Exception as e:
                self.logger.error(f"停止数据流协调器失败: {e}", exc_info=True)

        # 停止DecisionProvider
        if self._decision_manager:
            provider = self._decision_manager.get_current_provider()
            if hasattr(provider, "disconnect"):
                try:
                    await provider.disconnect()
                    self.logger.info("DecisionProvider 连接已断开")
                except Exception as e:
                    self.logger.error(f"DecisionProvider 断开失败: {e}", exc_info=True)

        # 停止HTTP服务器
        if self._http_server and self._http_server.is_running:
            try:
                await self._http_server.stop()
                self.logger.info("HTTP服务器已停止")
            except Exception as e:
                self.logger.error(f"停止HTTP服务器失败: {e}", exc_info=True)

        self.logger.info("核心服务已停止")

    def get_context_manager(self) -> ContextManager:
        """获取上下文管理器实例"""
        return self._context_manager

    @property
    def decision_manager(self) -> Optional[DecisionManager]:
        """获取决策管理器实例"""
        return self._decision_manager

    def set_decision_manager(self, decision_manager: DecisionManager):
        """设置决策管理器"""
        self._decision_manager = decision_manager
        self.logger.info("决策管理器已设置")
