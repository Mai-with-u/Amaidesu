"""
服务管理器测试

测试 ServiceManager 的基本功能：
- 服务注册和获取
- 服务状态查询
- 服务生命周期管理
"""

import pytest
from src.services.manager import ServiceManager


class MockService:
    """模拟服务类"""

    def __init__(self):
        self.setup_called = False
        self.cleanup_called = False
        self.is_ready_value = True

    async def setup(self):
        """初始化服务"""
        self.setup_called = True

    async def cleanup(self):
        """清理服务"""
        self.cleanup_called = True

    def is_ready(self) -> bool:
        """检查服务是否就绪"""
        return self.is_ready_value


class TestServiceManager:
    """服务管理器测试套件"""

    def test_service_manager_creation(self):
        """测试服务管理器创建"""
        manager = ServiceManager()
        assert manager is not None
        assert len(manager.list_services()) == 0

    def test_register_service(self):
        """测试服务注册"""
        manager = ServiceManager()
        service = MockService()

        manager.register("test_service", service)

        assert manager.has("test_service")
        assert "test_service" in manager.list_services()

    def test_get_service(self):
        """测试获取服务"""
        manager = ServiceManager()
        service = MockService()

        manager.register("test_service", service)
        retrieved = manager.get("test_service")

        assert retrieved is service
        assert retrieved is not None

    def test_get_nonexistent_service(self):
        """测试获取不存在的服务"""
        manager = ServiceManager()

        retrieved = manager.get("nonexistent")

        assert retrieved is None

    def test_has_service(self):
        """测试检查服务是否存在"""
        manager = ServiceManager()
        service = MockService()

        assert not manager.has("test_service")

        manager.register("test_service", service)

        assert manager.has("test_service")

    def test_is_ready_service(self):
        """测试检查服务是否就绪"""
        manager = ServiceManager()
        service = MockService()

        manager.register("test_service", service)

        assert manager.is_ready("test_service")

    def test_is_ready_nonexistent_service(self):
        """测试检查不存在的服务是否就绪"""
        manager = ServiceManager()

        assert not manager.is_ready("nonexistent")

    def test_service_without_is_ready_method(self):
        """测试没有 is_ready 方法的服务"""
        manager = ServiceManager()
        service = object()  # 没有 is_ready 方法的对象

        manager.register("test_service", service)

        # 如果没有 is_ready 方法，应该返回 True（认为已注册就是就绪）
        assert manager.is_ready("test_service")

    def test_unregister_service(self):
        """测试注销服务"""
        manager = ServiceManager()
        service = MockService()

        manager.register("test_service", service)
        assert manager.has("test_service")

        result = manager.unregister("test_service")

        assert result is True
        assert not manager.has("test_service")

    def test_unregister_nonexistent_service(self):
        """测试注销不存在的服务"""
        manager = ServiceManager()

        result = manager.unregister("nonexistent")

        assert result is False

    def test_register_service_with_config(self):
        """测试注册服务时附带配置"""
        manager = ServiceManager()
        service = MockService()
        config = {"option1": "value1", "option2": 42}

        manager.register("test_service", service, config=config)

        assert manager.has("test_service")
        service_info = manager.get_service_info()
        assert "test_service" in service_info
        assert service_info["test_service"]["config"] == config

    @pytest.mark.asyncio
    async def test_setup_service(self):
        """测试初始化服务"""
        manager = ServiceManager()
        service = MockService()

        manager.register("test_service", service)

        result = await manager.setup_service("test_service")

        assert result is True
        assert service.setup_called is True

    @pytest.mark.asyncio
    async def test_cleanup_service(self):
        """测试清理服务"""
        manager = ServiceManager()
        service = MockService()

        manager.register("test_service", service)

        result = await manager.cleanup_service("test_service")

        assert result is True
        assert service.cleanup_called is True

    @pytest.mark.asyncio
    async def test_setup_all_services(self):
        """测试初始化所有服务"""
        manager = ServiceManager()
        service1 = MockService()
        service2 = MockService()

        manager.register("service1", service1)
        manager.register("service2", service2)

        await manager.setup_all()

        assert service1.setup_called is True
        assert service2.setup_called is True

    @pytest.mark.asyncio
    async def test_cleanup_all_services(self):
        """测试清理所有服务"""
        manager = ServiceManager()
        service1 = MockService()
        service2 = MockService()

        manager.register("service1", service1)
        manager.register("service2", service2)

        await manager.cleanup_all()

        assert service1.cleanup_called is True
        assert service2.cleanup_called is True

    @pytest.mark.asyncio
    async def test_cleanup_all_services_in_reverse_order(self):
        """测试清理服务时按相反顺序进行"""
        manager = ServiceManager()
        service1 = MockService()
        service2 = MockService()

        cleanup_order = []

        # 修改 cleanup 方法来记录调用顺序
        original_cleanup1 = service1.cleanup
        original_cleanup2 = service2.cleanup

        async def cleanup1():
            cleanup_order.append("service1")
            await original_cleanup1()

        async def cleanup2():
            cleanup_order.append("service2")
            await original_cleanup2()

        service1.cleanup = cleanup1
        service2.cleanup = cleanup2

        manager.register("service1", service1)
        manager.register("service2", service2)

        await manager.cleanup_all()

        # 应该按相反顺序清理：service2 -> service1
        assert cleanup_order == ["service2", "service1"]

    def test_list_services(self):
        """测试列出所有服务"""
        manager = ServiceManager()
        service1 = MockService()
        service2 = MockService()

        manager.register("service1", service1)
        manager.register("service2", service2)

        services = manager.list_services()

        assert len(services) == 2
        assert "service1" in services
        assert "service2" in services

    def test_get_service_info(self):
        """测试获取服务信息"""
        manager = ServiceManager()
        service = MockService()
        config = {"option": "value"}

        manager.register("test_service", service, config=config)

        info = manager.get_service_info()

        assert "test_service" in info
        assert info["test_service"]["ready"] is True
        assert info["test_service"]["config"] == config


class TestGlobalServiceManager:
    """全局服务管理器测试"""

    def test_get_global_service_manager(self):
        """测试获取全局服务管理器"""
        from src.services.manager import get_global_service_manager

        manager1 = get_global_service_manager()
        manager2 = get_global_service_manager()

        # 应该返回同一个实例
        assert manager1 is manager2

    def test_global_service_functions(self):
        """测试全局服务函数"""
        from src.services import get_service, has_service, list_services
        from src.services.manager import get_global_service_manager

        manager = get_global_service_manager()
        service = MockService()

        manager.register("test_service", service)

        assert has_service("test_service")
        assert get_service("test_service") is service
        assert "test_service" in list_services()
