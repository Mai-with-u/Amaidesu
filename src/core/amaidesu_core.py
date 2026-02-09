"""
Amaidesu Core - 核心模块（Phase 3-4重构版本）

职责: 组件组合（Composition Root）
注意: 数据流处理已迁移到OutputCoordinator（A-01重构完成）
"""

from typing import Optional, Any

from src.core.utils.logger import get_logger
from src.domains.input.pipelines.manager import InputPipelineManager
from src.services.context.manager import ContextManager
from src.core.event_bus import EventBus
from src.domains.decision import DecisionProviderManager
from src.domains.output import OutputCoordinator

# LLM 服务（核心基础设施）
from src.services.llm.manager import LLMManager

# 服务管理器
from src.services.manager import ServiceManager


class AmaidesuCore:
    """Amaidesu 核心模块 - 组件组合根（Composition Root）"""

    @property
    def event_bus(self) -> Optional[EventBus]:
        """获取事件总线实例"""
        return self._event_bus

    @property
    def llm_service(self) -> Optional[LLMManager]:
        """获取 LLM 服务实例"""
        return self._llm_service

    @property
    def input_pipeline_manager(self) -> Optional[InputPipelineManager]:
        """获取输入管道管理器实例"""
        return self._input_pipeline_manager

    @property
    def output_coordinator(self) -> Optional[OutputCoordinator]:
        """获取输出协调器实例"""
        return self._output_coordinator

    @property
    def service_manager(self) -> ServiceManager:
        """获取服务管理器实例"""
        return self._service_manager

    def __init__(
        self,
        platform: str,
        pipeline_manager: Optional[InputPipelineManager] = None,
        context_manager: Optional[ContextManager] = None,
        event_bus: Optional[EventBus] = None,
        llm_service: Optional[LLMManager] = None,
        decision_provider_manager: Optional[DecisionProviderManager] = None,
        output_coordinator: Optional[OutputCoordinator] = None,
    ):
        """
        初始化 Amaidesu Core（A-01重构版本 - 纯组合根）。

        Args:
            platform: 平台标识符 (例如 "amaidesu")。
            pipeline_manager: (可选) 已配置的输入管道管理器。
            context_manager: (可选) 已配置的上下文管理器。
            event_bus: (可选) 已配置的事件总线。
            llm_service: (可选) 已配置的 LLM 服务。
            decision_provider_manager: (可选) 已配置的决策Provider管理器。
            output_coordinator: (可选) 已配置的输出协调器。
        """
        self.logger = get_logger("AmaidesuCore")
        self.logger.debug("AmaidesuCore 初始化开始")

        self.platform = platform

        # 初始化服务管理器
        self._service_manager = ServiceManager()
        self.logger.info("服务管理器已创建")

        # 输入管道管理器
        self._input_pipeline_manager = pipeline_manager
        if self._input_pipeline_manager is None:
            self.logger.info("输入管道处理功能已禁用")
        else:
            self.logger.info("输入管道处理功能已启用")

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
            # 注册到服务管理器
            self._service_manager.register("llm", llm_service)
            self.logger.info("已使用外部提供的 LLM 服务")
        else:
            self.logger.warning("未提供 LLM 服务，LLM 相关功能将不可用")

        # 设置决策Provider管理器
        self._decision_provider_manager = decision_provider_manager
        if decision_provider_manager is not None:
            self.logger.info("已使用外部提供的决策Provider管理器")

        # 设置输出协调器
        self._output_coordinator = output_coordinator
        if output_coordinator is not None:
            self.logger.info("已使用外部提供的输出协调器")

        self.logger.debug("AmaidesuCore 初始化完成")

    async def connect(self):
        """启动核心服务"""
        # 启动决策Provider（如果已配置）
        if self._decision_provider_manager:
            provider = self._decision_provider_manager.get_current_provider()
            if hasattr(provider, "connect"):
                try:
                    await provider.connect()
                    self.logger.info("决策Provider连接已启动")
                except Exception as e:
                    self.logger.error(f"决策Provider连接失败: {e}", exc_info=True)

        # 启动输出协调器
        if self._output_coordinator:
            try:
                await self._output_coordinator.start()
                self.logger.info("输出协调器已启动")
            except Exception as e:
                self.logger.error(f"启动输出协调器失败: {e}", exc_info=True)

    async def disconnect(self):
        """停止核心服务"""
        # 停止输出协调器
        if self._output_coordinator:
            try:
                await self._output_coordinator.stop()
                self.logger.info("输出协调器已停止")
            except Exception as e:
                self.logger.error(f"停止输出协调器失败: {e}", exc_info=True)

        # 停止决策Provider
        if self._decision_provider_manager:
            provider = self._decision_provider_manager.get_current_provider()
            if hasattr(provider, "disconnect"):
                try:
                    await provider.disconnect()
                    self.logger.info("决策Provider连接已断开")
                except Exception as e:
                    self.logger.error(f"决策Provider断开失败: {e}", exc_info=True)

        # 清理所有注册的服务
        try:
            await self._service_manager.cleanup_all()
            self.logger.info("所有服务已清理")
        except Exception as e:
            self.logger.error(f"清理服务时出错: {e}", exc_info=True)

        self.logger.info("核心服务已停止")

    def get_context_manager(self) -> ContextManager:
        """获取上下文管理器实例"""
        return self._context_manager

    @property
    def decision_provider_manager(self) -> Optional[DecisionProviderManager]:
        """获取决策Provider管理器实例"""
        return self._decision_provider_manager

    def set_decision_provider_manager(self, decision_provider_manager: DecisionProviderManager):
        """设置决策Provider管理器"""
        self._decision_provider_manager = decision_provider_manager
        self.logger.info("决策Provider管理器已设置")

    # 向后兼容属性
    @property
    def decision_manager(self) -> Optional[DecisionProviderManager]:
        """获取决策Provider管理器实例（向后兼容别名）"""
        return self._decision_provider_manager

    def set_decision_manager(self, decision_manager: DecisionProviderManager):
        """设置决策Provider管理器（向后兼容别名）"""
        self.set_decision_provider_manager(decision_manager)

    # === 服务管理方法 ===

    def register_service(self, name: str, service: Any) -> None:
        """
        注册服务到服务管理器

        Args:
            name: 服务名称
            service: 服务实例

        示例:
            ```python
            core.register_service("dg_lab", dg_lab_service)
            ```
        """
        self._service_manager.register(name, service)
        self.logger.info(f"服务 '{name}' 已注册到核心")

    def get_service(self, name: str) -> Optional[Any]:
        """
        获取服务实例

        Args:
            name: 服务名称

        Returns:
            服务实例，如果服务不存在则返回 None

        示例:
            ```python
            dg_lab = core.get_service("dg_lab")
            if dg_lab:
                await dg_lab.trigger_shock(strength=10)
            ```
        """
        return self._service_manager.get(name)

    def has_service(self, name: str) -> bool:
        """
        检查服务是否已注册

        Args:
            name: 服务名称

        Returns:
            bool: 服务是否已注册
        """
        return self._service_manager.has(name)

    def is_service_ready(self, name: str) -> bool:
        """
        检查服务是否就绪

        Args:
            name: 服务名称

        Returns:
            bool: 服务是否就绪
        """
        return self._service_manager.is_ready(name)

    def list_services(self) -> list[str]:
        """
        列出所有已注册的服务名称

        Returns:
            服务名称列表
        """
        return self._service_manager.list_services()
