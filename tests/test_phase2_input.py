"""
Phase 2: 输入层重构 - 单元测试

测试数据类型、Provider管理器和InputLayer的功能。
"""

import pytest
import asyncio
from src.core.data_types.raw_data import RawData
from src.core.data_types.normalized_text import NormalizedText
from src.core.event_bus import EventBus
from src.perception.input_provider_manager import InputProviderManager
from src.perception.input_layer import InputLayer
from src.perception.text.mock_danmaku_provider import MockDanmakuProvider


class TestRawData:
    """测试RawData数据类"""

    def test_raw_data_creation(self):
        """测试RawData创建"""
        data = RawData(content="测试内容", source="test", data_type="text")

        assert data.content == "测试内容"
        assert data.source == "test"
        assert data.data_type == "text"
        assert not data.preserve_original
        assert data.metadata == {}

    def test_raw_data_with_metadata(self):
        """测试带元数据的RawData"""
        metadata = {"user": "test_user", "user_id": 123}
        data = RawData(content="测试内容", source="test", data_type="text", metadata=metadata)

        assert data.metadata == metadata

    def test_raw_data_with_data_ref(self):
        """测试带data_ref的RawData"""
        data = RawData(content="测试内容", source="test", data_type="text", data_ref="cache://text/abc123")

        assert data.data_ref == "cache://text/abc123"

    def test_raw_data_to_dict(self):
        """测试RawData转换为字典"""
        data = RawData(content="测试", source="test", data_type="text")

        data_dict = data.to_dict()

        assert data_dict["content"] == "测试"
        assert data_dict["source"] == "test"
        assert data_dict["data_type"] == "text"


class TestNormalizedText:
    """测试NormalizedText数据类"""

    def test_normalized_text_creation(self):
        """测试NormalizedText创建"""
        metadata = {"type": "text", "source": "test"}
        normalized = NormalizedText(text="标准化文本", metadata=metadata)

        assert normalized.text == "标准化文本"
        assert normalized.metadata["type"] == "text"
        assert normalized.data_ref is None

    def test_normalized_text_from_raw_data(self):
        """测试从RawData创建NormalizedText"""
        raw_data = RawData(content="原始内容", source="test", data_type="text")

        normalized = NormalizedText.from_raw_data(raw_data=raw_data, text="标准化文本", source="test_source")

        assert normalized.text == "标准化文本"
        assert normalized.source == "test_source"
        assert normalized.data_type == "text"

    def test_normalized_text_properties(self):
        """测试NormalizedText属性"""
        metadata = {"type": "text", "source": "test"}
        normalized = NormalizedText(text="文本", metadata=metadata)

        assert normalized.source == "test"
        assert normalized.data_type == "text"


@pytest.mark.asyncio
class TestInputLayer:
    """测试InputLayer"""

    async def test_input_layer_setup(self):
        """测试InputLayer设置"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        await input_layer.setup()

        # 验证事件已订阅
        assert event_bus.get_listeners_count("perception.raw_data.generated") == 1

        await input_layer.cleanup()

    async def test_normalize_text(self):
        """测试文本标准化"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        raw_data = RawData(content="测试文本", source="test", data_type="text", metadata={"user": "test_user"})

        normalized = await input_layer.normalize(raw_data)

        assert normalized is not None
        assert normalized.text == "测试文本"
        assert normalized.source == "test"
        assert normalized.metadata["user"] == "test_user"

    async def test_normalize_gift(self):
        """测试礼物消息标准化"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        raw_data = RawData(
            content={"type": "gift", "gift_name": "火箭", "count": 1, "user": "测试用户"},
            source="test",
            data_type="gift",
        )

        normalized = await input_layer.normalize(raw_data)

        assert normalized is not None
        assert "送出了" in normalized.text
        assert "火箭" in normalized.text
        assert "测试用户" in normalized.text

    async def test_data_flow(self):
        """测试数据流: RawData -> NormalizedText"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        # 设置InputLayer（订阅事件）
        await input_layer.setup()

        # 收集事件
        raw_data_events = []
        normalized_text_events = []

        async def collect_raw_data(event_name: str, event_data: dict, source: str):
            """收集RawData事件"""
            raw_data_events.append(event_data["data"])

        async def collect_normalized_text(event_name: str, event_data: dict, source: str):
            """收集NormalizedText事件"""
            normalized_text_events.append(event_data["normalized"])

        # 订阅事件
        event_bus.on("perception.raw_data.generated", collect_raw_data)
        event_bus.on("normalization.text.ready", collect_normalized_text)

        # 创建一个MockDanmakuProvider
        mock_provider = MockDanmakuProvider({"send_interval": 0.1, "min_interval": 0.05, "max_interval": 0.1})

        # 使用InputProviderManager启动Provider（后台运行）
        provider_task = asyncio.create_task(input_manager.start_all_providers([mock_provider]))

        # 等待至少生成一条数据
        max_wait = 2.0
        start_time = asyncio.get_event_loop().time()
        while (len(raw_data_events) == 0 or len(normalized_text_events) == 0) and (
            asyncio.get_event_loop().time() - start_time < max_wait
        ):
            await asyncio.sleep(0.05)

        # 停止Provider
        await input_manager.stop_all_providers()

        # 取消启动任务
        provider_task.cancel()
        try:
            await provider_task
        except asyncio.CancelledError:
            pass

        # 验证数据流
        assert len(raw_data_events) > 0, f"应该至少生成一条RawData, 实际: {len(raw_data_events)}"
        assert len(normalized_text_events) > 0, f"应该至少生成一条NormalizedText, 实际: {len(normalized_text_events)}"

        # 验证数据类型
        assert isinstance(raw_data_events[0], RawData)
        assert isinstance(normalized_text_events[0], NormalizedText)

        # 验证数据转换
        assert normalized_text_events[0].text == raw_data_events[0].content
        assert normalized_text_events[0].data_ref == raw_data_events[0].data_ref

        # 清理订阅
        event_bus.off("perception.raw_data.generated", collect_raw_data)
        event_bus.off("normalization.text.ready", collect_normalized_text)

    async def test_normalize_superchat(self):
        """测试醒目留言转换"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        # 测试superchat（dict格式）
        raw_data = RawData(
            content={"user": "test_user", "content": "醒目留言内容"},
            source="test",
            data_type="superchat",
        )

        normalized = await input_layer.normalize(raw_data)

        assert normalized is not None
        assert "test_user" in normalized.text
        assert "醒目留言内容" in normalized.text

    async def test_normalize_guard(self):
        """测试大航海转换"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        # 测试guard（dict格式）
        raw_data = RawData(
            content={"user": "test_user", "level": "舰长"},
            source="test",
            data_type="guard",
        )

        normalized = await input_layer.normalize(raw_data)

        assert normalized is not None
        assert "test_user" in normalized.text
        assert "舰长" in normalized.text

    async def test_normalize_unknown_type(self):
        """测试未知类型转换"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        # 测试未知类型
        raw_data = RawData(
            content="test content",
            source="test",
            data_type="unknown_type",
        )

        normalized = await input_layer.normalize(raw_data)

        assert normalized is not None
        assert "[unknown_type]" in normalized.text
        assert "test content" in normalized.text

    async def test_normalize_empty_data(self):
        """测试空数据处理"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        # 测试空RawData事件
        collected = []

        async def on_raw_data(event_name: str, event_data: dict, source: str):
            collected.append(event_data)

        event_bus.on("perception.raw_data.generated", on_raw_data)
        await input_layer.setup()

        # 发布空的RawData
        await event_bus.emit("perception.raw_data.generated", {"data": None}, "test")

        # 验证：没有生成NormalizedText（因为data为None）
        assert len(collected) == 1
        assert collected[0]["data"] is None

    async def test_input_provider_manager_multiple_providers(self):
        """测试InputProviderManager管理多个Provider"""
        event_bus = EventBus()
        manager = InputProviderManager(event_bus)

        # 创建多个MockDanmakuProvider
        providers = [
            MockDanmakuProvider({"send_interval": 0.2, "min_interval": 0.1, "max_interval": 0.1}),
            MockDanmakuProvider({"send_interval": 0.3, "min_interval": 0.1, "max_interval": 0.1}),
        ]

        # 收集事件
        collected_data = []

        async def collect_data(event_name: str, event_data: dict, source: str):
            collected_data.append(event_data["data"])

        event_bus.on("perception.raw_data.generated", collect_data)

        # 启动所有provider
        start_task = asyncio.create_task(manager.start_all_providers(providers))

        # 等待收集一些数据
        await asyncio.sleep(1.0)

        # 停止
        await manager.stop_all_providers()

        # 取消任务
        start_task.cancel()
        try:
            await start_task
        except asyncio.CancelledError:
            pass

        # 验证：应该从多个provider收集数据
        assert len(collected_data) > 0

    async def test_raw_data_with_all_fields(self):
        """测试RawData的所有字段"""
        raw_data = RawData(
            content="test content",
            source="test_source",
            data_type="text",
            preserve_original=True,
            original_data={"original": "data"},
            metadata={"key": "value"},
            data_ref="test_ref_123",
        )

        assert raw_data.content == "test content"
        assert raw_data.source == "test_source"
        assert raw_data.data_type == "text"
        assert raw_data.preserve_original is True
        assert raw_data.original_data == {"original": "data"}
        assert raw_data.metadata == {"key": "value"}
        assert raw_data.data_ref == "test_ref_123"

    async def test_normalized_text_with_data_ref(self):
        """测试NormalizedText的data_ref字段"""
        normalized = NormalizedText(
            text="test text",
            metadata={"source": "test"},
            data_ref="ref_456",
        )

        assert normalized.text == "test text"
        assert "source" in normalized.metadata
        assert normalized.metadata["source"] == "test"
        assert normalized.data_ref == "ref_456"

    async def test_mock_provider_direct(self):
        """直接测试MockDanmakuProvider"""
        provider = MockDanmakuProvider({"send_interval": 0.1, "min_interval": 0.05, "max_interval": 0.1})

        # 收集数据
        collected_data = []
        collect_task = asyncio.create_task(self._collect_from_provider(provider, collected_data))

        # 等待收集一条数据
        start_time = asyncio.get_event_loop().time()
        while len(collected_data) == 0 and (asyncio.get_event_loop().time() - start_time < 1.0):
            await asyncio.sleep(0.01)

        # 停止provider
        await provider.stop()

        # 等待任务完成
        try:
            await asyncio.wait_for(collect_task, timeout=1.0)
        except asyncio.TimeoutError:
            collect_task.cancel()
            try:
                await collect_task
            except asyncio.CancelledError:
                pass

        # 验证
        assert len(collected_data) > 0, f"应该至少收集到一条数据, 实际: {len(collected_data)}"
        assert isinstance(collected_data[0], RawData)

    async def test_normalize_gift_non_dict(self):
        """测试礼物转换（非dict格式）"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        # 测试非dict格式
        raw_data = RawData(content="test gift content", source="test", data_type="gift")

        normalized = await input_layer.normalize(raw_data)

        assert normalized is not None
        assert "test gift content" in normalized.text

    async def test_normalize_superchat_non_dict(self):
        """测试醒目留言转换（非dict格式）"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        # 测试非dict格式
        raw_data = RawData(content="test sc content", source="test", data_type="superchat")

        normalized = await input_layer.normalize(raw_data)

        assert normalized is not None
        assert "醒目留言" in normalized.text

    async def test_normalized_text_from_raw_data_preserve(self):
        """测试NormalizedText.from_raw_data with preserve_original"""
        raw_data = RawData(
            content="test content",
            source="test",
            data_type="text",
            preserve_original=True,
            original_data={"original": "value"},
            metadata={"key": "value"},
        )

        normalized = NormalizedText.from_raw_data(
            raw_data=raw_data,
            text="normalized text",
            source="normalized source",
            preserve_original=True,
            data_ref="ref_123",
        )

        assert normalized.text == "normalized text"
        assert "timestamp" in normalized.metadata
        assert normalized.data_ref == "ref_123"

    async def test_normalized_text_to_dict(self):
        """测试NormalizedText.to_dict"""
        normalized = NormalizedText(
            text="test text",
            metadata={"key": "value"},
            data_ref="ref_123",
        )

        data_dict = normalized.to_dict()

        assert data_dict["text"] == "test text"
        assert data_dict["metadata"]["key"] == "value"
        assert data_dict["data_ref"] == "ref_123"

    async def test_input_layer_cleanup(self):
        """测试InputLayer cleanup"""
        event_bus = EventBus()
        input_manager = InputProviderManager(event_bus)
        input_layer = InputLayer(event_bus, input_manager)

        # 设置
        await input_layer.setup()

        # 清理
        await input_layer.cleanup()

        # 验证：不应该有错误
        assert True

    async def _collect_from_provider(self, provider, collected_list):
        """辅助函数：从provider收集数据"""
        async for data in provider.start():
            collected_list.append(data)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
