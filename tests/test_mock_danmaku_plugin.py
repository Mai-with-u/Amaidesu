"""
Mock Danmaku Plugin 测试

测试MockDanmakuPlugin和MockDanmakuInputProvider的功能。
"""

import pytest
import json
import tempfile
from pathlib import Path

from src.plugins.mock_danmaku.mock_danmaku_input_provider import MockDanmakuInputProvider
from src.plugins.mock_danmaku.plugin import MockDanmakuPlugin
from src.core.data_types.raw_data import RawData
from tests.test_plugin_utils import MockEventBus


class TestMockDanmakuInputProvider:
    """测试MockDanmakuInputProvider"""

    @pytest.fixture
    def test_config(self):
        """提供测试配置"""
        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            # 写入测试数据
            test_messages = [
                {"message": "测试消息1", "user": "user1"},
                {"message": "测试消息2", "user": "user2"},
                {"message": "测试消息3", "user": "user3"},
            ]
            for msg in test_messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

            temp_file_path = f.name

        return {
            "log_file_path": Path(temp_file_path).name,
            "send_interval": 0.1,
            "loop_playback": False,
            "start_immediately": True,
        }

    @pytest.mark.asyncio
    async def test_provider_init(self, test_config):
        """测试Provider初始化"""
        provider = MockDanmakuInputProvider(test_config)

        assert provider.send_interval == 0.1
        assert provider.loop_playback == False
        assert provider.start_immediately == True

    @pytest.mark.asyncio
    async def test_provider_collect_data(self, test_config):
        """测试数据采集"""
        provider = MockDanmakuInputProvider(test_config)

        collected_data = []
        async for data in provider.start():
            collected_data.append(data)

        # 验证采集到3条数据
        assert len(collected_data) == 3

        # 验证数据类型
        for data in collected_data:
            assert isinstance(data, RawData)
            assert data.source == "mock_danmaku"
            assert data.data_type == "message"

    @pytest.mark.asyncio
    async def test_provider_loop_playback(self, test_config):
        """测试循环播放"""
        test_config["loop_playback"] = True
        test_config["send_interval"] = 0.05

        provider = MockDanmakuInputProvider(test_config)

        collected_data = []
        async for data in provider.start():
            collected_data.append(data)
            if len(collected_data) >= 6:  # 收集6条数据后停止
                break

        # 验证循环播放（应该收集到6条数据）
        assert len(collected_data) == 6


class TestMockDanmakuPlugin:
    """测试MockDanmakuPlugin"""

    @pytest.fixture
    def plugin_config(self):
        """提供插件配置"""
        return {
            "enabled": True,
            "log_file_path": "msg_default.jsonl",
            "send_interval": 0.1,
            "loop_playback": True,
            "start_immediately": False,  # 手动启动，避免测试冲突
        }

    @pytest.fixture
    def event_bus(self):
        """提供EventBus"""
        return MockEventBus()

    @pytest.mark.asyncio
    async def test_plugin_init(self, plugin_config):
        """测试Plugin初始化"""
        plugin = MockDanmakuPlugin(plugin_config)

        assert plugin.config == plugin_config
        assert plugin._providers == []

    @pytest.mark.asyncio
    async def test_plugin_setup(self, plugin_config, event_bus):
        """测试Plugin设置"""
        plugin = MockDanmakuPlugin(plugin_config)
        providers = await plugin.setup(event_bus, plugin_config)

        # 验证返回的Provider列表
        assert len(providers) == 1
        assert isinstance(providers[0], MockDanmakuInputProvider)

        # 验证Provider已添加到插件
        assert len(plugin._providers) == 1

    @pytest.mark.asyncio
    async def test_plugin_cleanup(self, plugin_config, event_bus):
        """测试Plugin清理"""
        plugin = MockDanmakuPlugin(plugin_config)
        providers = await plugin.setup(event_bus, plugin_config)

        # 清理
        await plugin.cleanup()

        # 验证Provider已清理
        assert len(plugin._providers) == 0

    @pytest.mark.asyncio
    async def test_plugin_info(self, plugin_config):
        """测试Plugin信息"""
        plugin = MockDanmakuPlugin(plugin_config)
        info = plugin.get_info()

        # 验证基本信息
        assert info["name"] == "MockDanmaku"
        assert info["version"] == "1.0.0"
        assert info["category"] == "input"
        assert "author" in info
        assert "description" in info
        assert "api_version" in info

    @pytest.mark.asyncio
    async def test_plugin_disabled(self, plugin_config, event_bus):
        """测试插件被禁用的情况"""
        plugin_config["enabled"] = False
        plugin = MockDanmakuPlugin(plugin_config)
        providers = await plugin.setup(event_bus, plugin_config)

        # 验证没有返回Provider
        assert len(providers) == 0
        assert len(plugin._providers) == 0
