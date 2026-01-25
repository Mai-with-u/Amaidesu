"""
Extension系统单元测试

测试Extension接口、ExtensionManager和示例Extension
"""

import asyncio
import pytest
from typing import Any, Dict, List

from src.core.extensions import (
    BaseExtension,
    Extension,
    ExtensionInfo,
    ExtensionManager,
    ExtensionDependencyError,
    ExtensionLoadError,
)
from src.core.event_bus import EventBus


class TestExtensionInfo:
    """测试ExtensionInfo数据类"""

    def test_create_extension_info(self):
        """测试创建ExtensionInfo"""
        info = ExtensionInfo(
            name="test",
            version="1.0.0",
            description="Test extension",
            author="Test Author",
            dependencies=["dep1", "dep2"],
            enabled=True,
        )

        assert info.name == "test"
        assert info.version == "1.0.0"
        assert info.description == "Test extension"
        assert info.author == "Test Author"
        assert info.dependencies == ["dep1", "dep2"]
        assert info.enabled is True

    def test_default_values(self):
        """测试ExtensionInfo默认值"""
        info = ExtensionInfo(name="test")

        assert info.version == "1.0.0"
        assert info.description == ""
        assert info.author == ""
        assert info.dependencies == []
        assert info.providers == []
        assert info.enabled is True


class MockExtension(BaseExtension):
    """用于测试的Mock Extension"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.setup_called = False
        self.cleanup_called = False

    async def setup(self, event_bus: Any, config: Dict[str, Any]) -> List[Any]:
        self._event_bus = event_bus
        self.config = config
        self.setup_called = True
        return []

    async def cleanup(self) -> None:
        self.cleanup_called = True
        await super().cleanup()

    def get_info(self) -> ExtensionInfo:
        return ExtensionInfo(
            name="mock",
            version="1.0.0",
            description="Mock extension for testing",
            author="Test",
            dependencies=[],
            enabled=True,
        )


class MockExtensionWithDependencies(BaseExtension):
    """带有依赖的Mock Extension"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    async def setup(self, event_bus: Any, config: Dict[str, Any]) -> List[Any]:
        self._event_bus = event_bus
        self.config = config
        return []

    def get_info(self) -> ExtensionInfo:
        return ExtensionInfo(
            name="mock_with_deps",
            version="1.0.0",
            description="Mock extension with dependencies",
            author="Test",
            dependencies=self.get_dependencies(),
            enabled=True,
        )

    def get_dependencies(self) -> List[str]:
        return ["mock"]


class TestBaseExtension:
    """测试BaseExtension"""

    @pytest.mark.asyncio
    async def test_base_extension_setup(self):
        """测试BaseExtension.setup()"""
        event_bus = EventBus(enable_stats=False)
        extension = MockExtension({})

        await extension.setup(event_bus, {})

        assert extension.setup_called is True
        assert extension._event_bus is event_bus

    @pytest.mark.asyncio
    async def test_base_extension_cleanup(self):
        """测试BaseExtension.cleanup()"""
        event_bus = EventBus(enable_stats=False)
        extension = MockExtension({})

        await extension.setup(event_bus, {})
        await extension.cleanup()

        assert extension.cleanup_called is True
        assert extension._event_bus is None

    @pytest.mark.asyncio
    async def test_emit_event(self):
        """测试emit_event()"""
        event_bus = EventBus(enable_stats=False)
        extension = MockExtension({})

        await extension.setup(event_bus, {})

        event_received = []

        def handler(event_name, data, source):
            event_received.append(data)

        event_bus.on("test.event", handler)

        await extension.emit_event("test.event", "test_data")

        assert len(event_received) == 1
        assert event_received[0] == "test_data"

    @pytest.mark.asyncio
    async def test_listen_event(self):
        """测试listen_event()"""
        event_bus = EventBus(enable_stats=False)
        extension = MockExtension({})

        await extension.setup(event_bus, {})

        event_received = []

        def handler(event_name, data, source):
            event_received.append(data)

        extension.listen_event("test.event", handler)

        await event_bus.emit("test.event", "test_data", "test")

        assert len(event_received) == 1
        assert event_received[0] == "test_data"

    @pytest.mark.asyncio
    async def test_stop_listening_event(self):
        """测试stop_listening_event()"""
        event_bus = EventBus(enable_stats=False)
        extension = MockExtension({})

        await extension.setup(event_bus, {})

        event_received = []

        def handler(event_name, data, source):
            event_received.append(data)

        extension.listen_event("test.event", handler)
        await event_bus.emit("test.event", "data1", "test")

        extension.stop_listening_event("test.event", handler)
        await event_bus.emit("test.event", "data2", "test")

        assert len(event_received) == 1
        assert event_received[0] == "data1"

    def test_add_provider(self):
        """测试add_provider()"""
        extension = MockExtension({})
        mock_provider = object()

        extension.add_provider(mock_provider)

        assert len(extension._providers) == 1
        assert extension._providers[0] is mock_provider

    def test_remove_provider(self):
        """测试remove_provider()"""
        extension = MockExtension({})
        mock_provider = object()

        extension.add_provider(mock_provider)
        extension.remove_provider(mock_provider)

        assert len(extension._providers) == 0

    def test_get_info(self):
        """测试get_info()"""
        extension = MockExtension({})
        info = extension.get_info()

        assert isinstance(info, ExtensionInfo)
        assert info.name == "mock"

    def test_get_dependencies(self):
        """测试get_dependencies()"""
        extension = MockExtension({})
        deps = extension.get_dependencies()

        assert deps == []

    def test_get_dependencies_with_deps(self):
        """测试get_dependencies() - 带依赖"""
        extension = MockExtensionWithDependencies({})
        deps = extension.get_dependencies()

        assert deps == ["mock"]


class TestExtensionManager:
    """测试ExtensionManager"""

    @pytest.fixture
    def event_bus(self):
        """EventBus fixture"""
        return EventBus(enable_stats=False)

    @pytest.fixture
    def manager(self, event_bus):
        """ExtensionManager fixture"""
        return ExtensionManager(event_bus)

    @pytest.mark.asyncio
    async def test_create_manager(self, manager):
        """测试创建ExtensionManager"""
        assert manager is not None
        assert manager._event_bus is not None
        assert len(manager._extensions) == 0

    @pytest.mark.asyncio
    async def test_get_extension_not_found(self, manager):
        """测试get_extension() - Extension不存在"""
        extension = await manager.get_extension("nonexistent")
        assert extension is None

    @pytest.mark.asyncio
    async def test_unload_extension_not_found(self, manager):
        """测试unload_extension() - Extension不存在"""
        result = await manager.unload_extension("nonexistent")
        assert result is False

    def test_get_loaded_extensions_empty(self, manager):
        """测试get_loaded_extensions() - 空"""
        extensions = manager.get_loaded_extensions()
        assert extensions == []

    def test_get_extension_info_not_found(self, manager):
        """测试get_extension_info() - Extension不存在"""
        info = manager.get_extension_info("nonexistent")
        assert info is None

    def test_get_all_extension_infos_empty(self, manager):
        """测试get_all_extension_infos() - 空"""
        infos = manager.get_all_extension_infos()
        assert infos == {}

    @pytest.mark.asyncio
    async def test_has_circular_dependency_no_cycle(self, manager):
        """测试_has_circular_dependency() - 无环"""
        graph = {
            "a": ["b", "c"],
            "b": ["d"],
            "c": ["d"],
            "d": [],
        }

        result = manager._has_circular_dependency(graph)
        assert result is False

    @pytest.mark.asyncio
    async def test_has_circular_dependency_with_cycle(self, manager):
        """测试_has_circular_dependency() - 有环"""
        graph = {
            "a": ["b"],
            "b": ["c"],
            "c": ["a"],  # 循环依赖
        }

        result = manager._has_circular_dependency(graph)
        assert result is True

    @pytest.mark.asyncio
    async def test_topological_sort(self, manager):
        """测试拓扑排序"""
        graph = {
            "a": ["b", "c"],  # a依赖b和c（a需要b和c先被处理）
            "b": ["d"],  # b依赖d
            "c": ["d"],  # c依赖d
            "d": [],  # d没有依赖
        }

        result = manager._topological_sort(graph)

        # 检查拓扑排序的正确性：
        # - a依赖b和c，所以b和c应该在a之前
        # - b和c都依赖d，所以d应该在b和c之前
        # 结果应该是：d -> b/c -> a

        assert "a" in result
        assert "b" in result
        assert "c" in result
        assert "d" in result

        # 获取各节点在结果中的索引
        a_index = result.index("a")
        b_index = result.index("b")
        c_index = result.index("c")
        d_index = result.index("d")

        # 依赖关系检查：
        # d没有依赖，应该最先处理
        # b和c依赖d，应该在d之后处理
        # a依赖b和c，应该在b和c之后处理

        assert d_index < b_index, "d应该在b之前（b依赖d）"
        assert d_index < c_index, "d应该在c之前（c依赖d）"
        assert b_index < a_index, "b应该在a之前（a依赖b）"
        assert c_index < a_index, "c应该在a之前（a依赖c）"

    @pytest.mark.asyncio
    async def test_topological_sort_cycle(self, manager):
        """测试拓扑排序 - 有环"""
        graph = {
            "a": ["b"],
            "b": ["c"],
            "c": ["a"],  # 循环依赖
        }

        with pytest.raises(ExtensionDependencyError):
            manager._topological_sort(graph)

    @pytest.mark.asyncio
    async def test_get_dependents(self, manager):
        """测试_get_dependents()"""
        info1 = ExtensionInfo(
            name="ext1",
            dependencies=["common"],
        )
        info2 = ExtensionInfo(
            name="ext2",
            dependencies=["common"],
        )
        info3 = ExtensionInfo(
            name="ext3",
            dependencies=["ext1"],
        )

        manager._extension_infos = {
            "ext1": info1,
            "ext2": info2,
            "ext3": info3,
        }

        dependents = manager._get_dependents("common")

        assert len(dependents) == 2
        assert "ext1" in dependents
        assert "ext2" in dependents

    @pytest.mark.asyncio
    async def test_cleanup_all(self, manager):
        """测试cleanup_all()"""
        ext1 = MockExtension({})
        ext2 = MockExtension({})

        manager._extensions = {
            "ext1": ext1,
            "ext2": ext2,
        }

        await manager.cleanup_all()

        assert len(manager._extensions) == 0
        assert len(manager._extension_infos) == 0
        assert ext1.cleanup_called is True
        assert ext2.cleanup_called is True


@pytest.mark.asyncio
async def test_example_extension():
    """测试示例Extension"""
    from src.extensions.example.extension import ExampleExtension

    event_bus = EventBus(enable_stats=False)
    extension = ExampleExtension({})

    # 测试setup
    await extension.setup(event_bus, {})
    assert extension._is_setup is True

    # 测试get_info
    info = extension.get_info()
    assert info.name == "example"
    assert info.version == "1.0.0"
    assert len(info.dependencies) == 0

    # 测试cleanup
    await extension.cleanup()
    assert extension._is_setup is False
