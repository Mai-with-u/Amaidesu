"""
AmaidesuCore 单元测试

测试核心模块的功能：
- AmaidesuCore 初始化
- 组件依赖注入
- 生命周期方法
- 服务管理功能
- 属性访问

运行: uv run pytest tests/core/test_amaidesu_core.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.amaidesu_core import AmaidesuCore
from src.core.event_bus import EventBus
from src.services.llm.manager import LLMManager
from src.services.manager import ServiceManager
from src.domains.input.pipelines.manager import InputPipelineManager
from src.domains.decision import DecisionProviderManager
from src.domains.output import OutputCoordinator


# =============================================================================
# 测试 Fixtures
# =============================================================================


@pytest.fixture
def mock_event_bus():
    """创建模拟的 EventBus"""
    bus = MagicMock(spec=EventBus)
    bus.initialize = AsyncMock()
    bus.cleanup = AsyncMock()
    return bus


@pytest.fixture
def mock_llm_manager():
    """创建模拟的 LLMManager"""
    manager = MagicMock(spec=LLMManager)
    manager.setup = AsyncMock()
    manager.cleanup = AsyncMock()
    manager.is_ready = MagicMock(return_value=True)
    return manager


@pytest.fixture
def mock_input_pipeline_manager():
    """创建模拟的 InputPipelineManager"""
    manager = MagicMock(spec=InputPipelineManager)
    return manager


@pytest.fixture
def mock_decision_provider_manager():
    """创建模拟的 DecisionProviderManager"""
    manager = MagicMock(spec=DecisionProviderManager)
    manager.get_current_provider = MagicMock(return_value=MagicMock())
    return manager


@pytest.fixture
def mock_output_coordinator():
    """创建模拟的 OutputCoordinator"""
    coordinator = MagicMock(spec=OutputCoordinator)
    coordinator.start = AsyncMock()
    coordinator.stop = AsyncMock()
    return coordinator


# =============================================================================
# AmaidesuCore 初始化测试
# =============================================================================


class TestAmaidesuCoreInitialization:
    """测试 AmaidesuCore 初始化"""

    def test_initialization_with_platform_only(self):
        """测试只传入 platform 参数的初始化"""
        core = AmaidesuCore(platform="test_platform")
        assert core.platform == "test_platform"
        assert core.event_bus is not None
        assert isinstance(core.event_bus, EventBus)
        assert core.service_manager is not None
        assert isinstance(core.service_manager, ServiceManager)

    def test_initialization_with_event_bus(self, mock_event_bus):
        """测试传入自定义 EventBus 的初始化"""
        core = AmaidesuCore(platform="test", event_bus=mock_event_bus)
        assert core.event_bus == mock_event_bus

    def test_initialization_with_llm_service(self, mock_llm_manager):
        """测试传入 LLM 服务的初始化"""
        core = AmaidesuCore(platform="test", llm_service=mock_llm_manager)
        assert core.llm_service == mock_llm_manager
        # LLM 服务应该被注册到服务管理器
        assert core.has_service("llm")
        assert core.get_service("llm") == mock_llm_manager

    def test_initialization_with_input_pipeline_manager(self, mock_input_pipeline_manager):
        """测试传入 InputPipelineManager 的初始化"""
        core = AmaidesuCore(
            platform="test",
            pipeline_manager=mock_input_pipeline_manager,
        )
        assert core.input_pipeline_manager == mock_input_pipeline_manager

    def test_initialization_with_decision_provider_manager(self, mock_decision_provider_manager):
        """测试传入 DecisionProviderManager 的初始化"""
        core = AmaidesuCore(
            platform="test",
            decision_provider_manager=mock_decision_provider_manager,
        )
        assert core.decision_provider_manager == mock_decision_provider_manager

    def test_initialization_with_output_coordinator(self, mock_output_coordinator):
        """测试传入 OutputCoordinator 的初始化"""
        core = AmaidesuCore(
            platform="test",
            output_coordinator=mock_output_coordinator,
        )
        assert core.output_coordinator == mock_output_coordinator

    def test_initialization_with_all_components(
        self,
        mock_event_bus,
        mock_llm_manager,
        mock_input_pipeline_manager,
        mock_decision_provider_manager,
        mock_output_coordinator,
    ):
        """测试传入所有组件的完整初始化"""
        core = AmaidesuCore(
            platform="test",
            pipeline_manager=mock_input_pipeline_manager,
            event_bus=mock_event_bus,
            llm_service=mock_llm_manager,
            decision_provider_manager=mock_decision_provider_manager,
            output_coordinator=mock_output_coordinator,
        )
        assert core.platform == "test"
        assert core.event_bus == mock_event_bus
        assert core.llm_service == mock_llm_manager
        assert core.input_pipeline_manager == mock_input_pipeline_manager
        assert core.decision_provider_manager == mock_decision_provider_manager
        assert core.output_coordinator == mock_output_coordinator


# =============================================================================
# 属性访问测试
# =============================================================================


class TestAmaidesuCoreProperties:
    """测试 AmaidesuCore 属性访问"""

    def test_event_bus_property(self, mock_event_bus):
        """测试 event_bus 属性"""
        core = AmaidesuCore(platform="test", event_bus=mock_event_bus)
        assert core.event_bus == mock_event_bus

    def test_llm_service_property(self, mock_llm_manager):
        """测试 llm_service 属性"""
        core = AmaidesuCore(platform="test", llm_service=mock_llm_manager)
        assert core.llm_service == mock_llm_manager

    def test_llm_service_property_none(self):
        """测试未设置 LLM 服务时返回 None"""
        core = AmaidesuCore(platform="test")
        assert core.llm_service is None

    def test_input_pipeline_manager_property(self, mock_input_pipeline_manager):
        """测试 input_pipeline_manager 属性"""
        core = AmaidesuCore(
            platform="test",
            pipeline_manager=mock_input_pipeline_manager,
        )
        assert core.input_pipeline_manager == mock_input_pipeline_manager

    def test_input_pipeline_manager_property_none(self):
        """测试未设置 InputPipelineManager 时返回 None"""
        core = AmaidesuCore(platform="test")
        assert core.input_pipeline_manager is None

    def test_output_coordinator_property(self, mock_output_coordinator):
        """测试 output_coordinator 属性"""
        core = AmaidesuCore(
            platform="test",
            output_coordinator=mock_output_coordinator,
        )
        assert core.output_coordinator == mock_output_coordinator

    def test_output_coordinator_property_none(self):
        """测试未设置 OutputCoordinator 时返回 None"""
        core = AmaidesuCore(platform="test")
        assert core.output_coordinator is None

    def test_service_manager_property(self):
        """测试 service_manager 属性"""
        core = AmaidesuCore(platform="test")
        assert isinstance(core.service_manager, ServiceManager)

    def test_decision_provider_manager_property(self, mock_decision_provider_manager):
        """测试 decision_provider_manager 属性"""
        core = AmaidesuCore(
            platform="test",
            decision_provider_manager=mock_decision_provider_manager,
        )
        assert core.decision_provider_manager == mock_decision_provider_manager

    def test_decision_provider_manager_property_none(self):
        """测试未设置 DecisionProviderManager 时返回 None"""
        core = AmaidesuCore(platform="test")
        assert core.decision_provider_manager is None

    def test_decision_manager_backward_compatibility(self, mock_decision_provider_manager):
        """测试 decision_manager 向后兼容别名"""
        core = AmaidesuCore(
            platform="test",
            decision_provider_manager=mock_decision_provider_manager,
        )
        assert core.decision_manager == mock_decision_provider_manager


# =============================================================================
# 服务管理测试
# =============================================================================


class TestAmaidesuCoreServiceManagement:
    """测试 AmaidesuCore 服务管理功能"""

    def test_register_service(self):
        """测试注册服务"""
        core = AmaidesuCore(platform="test")
        mock_service = MagicMock()

        core.register_service("test_service", mock_service)

        assert core.has_service("test_service")
        assert core.get_service("test_service") == mock_service

    def test_get_service(self):
        """测试获取服务"""
        core = AmaidesuCore(platform="test")
        mock_service = MagicMock()

        core.register_service("test_service", mock_service)
        retrieved = core.get_service("test_service")

        assert retrieved == mock_service

    def test_get_service_nonexistent(self):
        """测试获取不存在的服务返回 None"""
        core = AmaidesuCore(platform="test")
        assert core.get_service("nonexistent") is None

    def test_has_service(self):
        """测试检查服务是否存在"""
        core = AmaidesuCore(platform="test")
        mock_service = MagicMock()

        assert core.has_service("test_service") is False

        core.register_service("test_service", mock_service)

        assert core.has_service("test_service") is True

    def test_is_service_ready(self):
        """测试检查服务是否就绪"""
        core = AmaidesuCore(platform="test")
        mock_service = MagicMock()
        mock_service.is_ready = MagicMock(return_value=True)

        core.register_service("test_service", mock_service)

        assert core.is_service_ready("test_service") is True

    def test_is_service_ready_no_is_ready_method(self):
        """测试服务没有 is_ready 方法时默认返回 True"""
        core = AmaidesuCore(platform="test")
        mock_service = MagicMock(spec=[])  # 没有 is_ready 方法

        core.register_service("test_service", mock_service)

        assert core.is_service_ready("test_service") is True

    def test_list_services(self):
        """测试列出所有服务"""
        core = AmaidesuCore(platform="test")

        core.register_service("service1", MagicMock())
        core.register_service("service2", MagicMock())
        core.register_service("service3", MagicMock())

        services = core.list_services()

        assert len(services) == 3
        assert "service1" in services
        assert "service2" in services
        assert "service3" in services


# =============================================================================
# DecisionProviderManager 设置测试
# =============================================================================


class TestAmaidesuCoreDecisionProviderManager:
    """测试 AmaidesuCore DecisionProviderManager 设置功能"""

    def test_set_decision_provider_manager(self):
        """测试设置 DecisionProviderManager"""
        core = AmaidesuCore(platform="test")
        mock_manager = MagicMock(spec=DecisionProviderManager)

        core.set_decision_provider_manager(mock_manager)

        assert core.decision_provider_manager == mock_manager

    def test_set_decision_manager_backward_compatibility(self):
        """测试 set_decision_manager 向后兼容别名"""
        core = AmaidesuCore(platform="test")
        mock_manager = MagicMock(spec=DecisionProviderManager)

        core.set_decision_manager(mock_manager)

        assert core.decision_provider_manager == mock_manager


# =============================================================================
# 生命周期方法测试
# =============================================================================


class TestAmaidesuCoreLifecycle:
    """测试 AmaidesuCore 生命周期方法"""

    @pytest.mark.asyncio
    async def test_connect_with_decision_provider(self, mock_decision_provider_manager):
        """测试 connect 启动决策 Provider"""
        mock_provider = MagicMock()
        mock_provider.connect = AsyncMock()
        mock_decision_provider_manager.get_current_provider = MagicMock(return_value=mock_provider)

        core = AmaidesuCore(
            platform="test",
            decision_provider_manager=mock_decision_provider_manager,
        )

        await core.connect()

        mock_provider.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_with_output_coordinator(self, mock_output_coordinator):
        """测试 connect 启动输出协调器"""
        core = AmaidesuCore(
            platform="test",
            output_coordinator=mock_output_coordinator,
        )

        await core.connect()

        mock_output_coordinator.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_with_all_components(
        self,
        mock_decision_provider_manager,
        mock_output_coordinator,
    ):
        """测试 connect 启动所有组件"""
        mock_provider = MagicMock()
        mock_provider.connect = AsyncMock()
        mock_decision_provider_manager.get_current_provider = MagicMock(return_value=mock_provider)

        core = AmaidesuCore(
            platform="test",
            decision_provider_manager=mock_decision_provider_manager,
            output_coordinator=mock_output_coordinator,
        )

        await core.connect()

        mock_provider.connect.assert_called_once()
        mock_output_coordinator.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_no_components(self):
        """测试没有组件时 connect 正常执行"""
        core = AmaidesuCore(platform="test")

        # 不应该抛出异常
        await core.connect()

    @pytest.mark.asyncio
    async def test_disconnect_with_output_coordinator(self, mock_output_coordinator):
        """测试 disconnect 停止输出协调器"""
        core = AmaidesuCore(
            platform="test",
            output_coordinator=mock_output_coordinator,
        )

        await core.disconnect()

        mock_output_coordinator.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_with_decision_provider(self, mock_decision_provider_manager):
        """测试 disconnect 停止决策 Provider"""
        mock_provider = MagicMock()
        mock_provider.disconnect = AsyncMock()
        mock_decision_provider_manager.get_current_provider = MagicMock(return_value=mock_provider)

        core = AmaidesuCore(
            platform="test",
            decision_provider_manager=mock_decision_provider_manager,
        )

        await core.disconnect()

        mock_provider.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_with_all_components(
        self,
        mock_decision_provider_manager,
        mock_output_coordinator,
    ):
        """测试 disconnect 停止所有组件"""
        mock_provider = MagicMock()
        mock_provider.disconnect = AsyncMock()
        mock_decision_provider_manager.get_current_provider = MagicMock(return_value=mock_provider)

        core = AmaidesuCore(
            platform="test",
            decision_provider_manager=mock_decision_provider_manager,
            output_coordinator=mock_output_coordinator,
        )

        await core.disconnect()

        mock_provider.disconnect.assert_called_once()
        mock_output_coordinator.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_no_components(self):
        """测试没有组件时 disconnect 正常执行"""
        core = AmaidesuCore(platform="test")

        # 不应该抛出异常
        await core.disconnect()


# =============================================================================
# 错误处理测试
# =============================================================================


class TestAmaidesuCoreErrorHandling:
    """测试 AmaidesuCore 错误处理"""

    @pytest.mark.asyncio
    async def test_connect_provider_connect_error(self, mock_decision_provider_manager, caplog):
        """测试 Provider 连接失败时的错误处理"""
        mock_provider = MagicMock()
        mock_provider.connect = AsyncMock(side_effect=Exception("Connection failed"))
        mock_decision_provider_manager.get_current_provider = MagicMock(return_value=mock_provider)

        core = AmaidesuCore(
            platform="test",
            decision_provider_manager=mock_decision_provider_manager,
        )

        # 不应该抛出异常，应该记录错误
        await core.connect()

        # 验证错误被记录（检查日志）
        # 注意：这需要实际的日志配置才能捕获

    @pytest.mark.asyncio
    async def test_connect_output_coordinator_error(self, mock_output_coordinator, caplog):
        """测试输出协调器启动失败时的错误处理"""
        mock_output_coordinator.start = AsyncMock(side_effect=Exception("Start failed"))

        core = AmaidesuCore(
            platform="test",
            output_coordinator=mock_output_coordinator,
        )

        # 不应该抛出异常，应该记录错误
        await core.connect()

    @pytest.mark.asyncio
    async def test_disconnect_provider_disconnect_error(self, mock_decision_provider_manager, caplog):
        """测试 Provider 断开失败时的错误处理"""
        mock_provider = MagicMock()
        mock_provider.disconnect = AsyncMock(side_effect=Exception("Disconnect failed"))
        mock_decision_provider_manager.get_current_provider = MagicMock(return_value=mock_provider)

        core = AmaidesuCore(
            platform="test",
            decision_provider_manager=mock_decision_provider_manager,
        )

        # 不应该抛出异常，应该记录错误
        await core.disconnect()


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
