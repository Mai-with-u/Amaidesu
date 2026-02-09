"""
AvatarOutputProvider 集成测试 - 验证 VRChat 适配器集成
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.domains.output.providers.avatar.avatar_output_provider import AvatarOutputProvider
from src.domains.output.parameters.render_parameters import RenderParameters


@pytest.fixture
def vrchat_config():
    """VRChat 完整配置"""
    return {
        "adapter_type": "vrchat",
        "vrc_host": "127.0.0.1",
        "vrc_in_port": 9001,
        "vrc_out_port": 9000,
        "enable_server": False,
        "auto_send_params": True,
    }


@pytest.fixture
def mock_event_bus():
    """Mock EventBus"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()
    return event_bus


@pytest.mark.asyncio
class TestAvatarVRChatIntegration:
    """测试 AvatarOutputProvider 与 VRChat 适配器的集成"""

    async def test_vrchat_adapter_creation(self, vrchat_config):
        """测试 VRChat 适配器创建"""
        from src.domains.output.adapter_factory import AdapterFactory

        adapter = AdapterFactory.create("vrchat", vrchat_config)

        assert adapter is not None
        assert adapter.adapter_name == "vrchat"

    async def test_vrchat_adapter_alias(self, vrchat_config):
        """测试 VRChat 适配器别名（vrc）"""
        from src.domains.output.adapter_factory import AdapterFactory

        adapter = AdapterFactory.create("vrc", vrchat_config)

        assert adapter is not None
        assert adapter.adapter_name == "vrchat"

    async def test_full_vrchat_workflow(self, vrchat_config):
        """测试完整的 VRChat 工作流"""
        # 创建 mock 适配器
        mock_adapter = MagicMock()
        mock_adapter.is_connected = True
        mock_adapter.set_parameters = AsyncMock(return_value=True)
        mock_adapter.connect = AsyncMock(return_value=True)
        mock_adapter.disconnect = AsyncMock(return_value=True)

        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            # 创建 Provider
            provider = AvatarOutputProvider(vrchat_config)

            # 验证初始化
            assert provider.adapter_type == "vrchat"

            # 初始化
            await provider._setup_internal()
            assert provider.adapter is not None
            mock_adapter.connect.assert_called_once()

            # 渲染表情参数
            render_params = RenderParameters(
                expressions_enabled=True,
                expressions={
                    "smile": 0.8,
                    "eye_open": 0.5,
                    "mouth_open": 0.3,
                },
            )

            await provider._render_internal(render_params)

            # 验证参数被翻译并设置
            # VRChat 适配器会将 smile 翻译为 MouthSmile
            mock_adapter.set_parameters.assert_called_once()
            call_args = mock_adapter.set_parameters.call_args[0][0]
            assert "MouthSmile" in call_args or "smile" in call_args

            # 清理
            await provider._cleanup_internal()
            mock_adapter.disconnect.assert_called_once()

    async def test_vrchat_parameter_translation(self, vrchat_config):
        """测试 VRChat 参数翻译"""
        from src.domains.output.adapter_factory import AdapterFactory

        adapter = AdapterFactory.create("vrchat", vrchat_config)

        # 测试参数翻译
        abstract_params = {
            "smile": 0.8,
            "eye_open": 0.5,
            "mouth_open": 0.3,
            "brow_down": 0.2,
        }

        translated = adapter.translate_params(abstract_params)

        # 验证翻译结果
        assert translated["MouthSmile"] == 0.8
        assert translated["EyeOpen"] == 0.5
        assert translated["MouthOpen"] == 0.3
        assert translated["BrowDownLeft"] == 0.2

    async def test_vrchat_gesture_triggering(self, vrchat_config):
        """测试 VRChat 手势触发"""
        from src.domains.output.adapter_factory import AdapterFactory

        adapter = AdapterFactory.create("vrchat", vrchat_config)

        # Mock OSC 客户端
        with patch("src.domains.output.adapters.vrchat.vrchat_adapter.SimpleUDPClient"):
            await adapter.connect()

            # 测试手势触发
            result = await adapter.trigger_hotkey("Wave")
            assert result is True

            # 测试无效手势
            result = await adapter.trigger_hotkey("InvalidGesture")
            assert result is False

            await adapter.disconnect()

    async def test_multiple_avatar_providers(self):
        """测试多个 Avatar Provider 实例"""
        vts_config = {
            "adapter_type": "vts",
            "vts_host": "127.0.0.1",
            "vts_port": 8001,
        }

        vrchat_config = {
            "adapter_type": "vrchat",
            "vrc_host": "127.0.0.1",
            "vrc_in_port": 9001,
            "vrc_out_port": 9000,
        }

        # Mock 适配器
        mock_vts_adapter = MagicMock()
        mock_vts_adapter.is_connected = True
        mock_vts_adapter.connect = AsyncMock(return_value=True)
        mock_vts_adapter.set_parameters = AsyncMock(return_value=True)

        mock_vrchat_adapter = MagicMock()
        mock_vrchat_adapter.is_connected = True
        mock_vrchat_adapter.connect = AsyncMock(return_value=True)
        mock_vrchat_adapter.set_parameters = AsyncMock(return_value=True)

        def create_mock_adapter(adapter_type, config):
            if adapter_type == "vts":
                return mock_vts_adapter
            elif adapter_type == "vrchat":
                return mock_vrchat_adapter
            return None

        with patch("src.domains.output.adapter_factory.AdapterFactory.create", side_effect=create_mock_adapter):
            # 创建 VTS Provider
            vts_provider = AvatarOutputProvider(vts_config)
            await vts_provider._setup_internal()

            # 创建 VRChat Provider
            vrchat_provider = AvatarOutputProvider(vrchat_config)
            await vrchat_provider._setup_internal()

            # 验证两个 Provider 都正确初始化
            assert vts_provider.adapter_type == "vts"
            assert vrchat_provider.adapter_type == "vrchat"

            # 验证它们使用不同的适配器
            assert vts_provider.adapter is not vrchat_provider.adapter

    async def test_adapter_factory_registration(self):
        """测试适配器工厂注册机制"""
        from src.domains.output.adapter_factory import AdapterFactory
        from src.domains.output.adapters.base import PlatformAdapter

        # 创建一个自定义适配器
        class CustomAdapter(PlatformAdapter):
            def __init__(self, config):
                super().__init__("custom", config)

            async def connect(self) -> bool:
                self._is_connected = True
                return True

            async def disconnect(self) -> bool:
                self._is_connected = False
                return True

            async def set_parameters(self, params: dict) -> bool:
                return True

        # 注册自定义适配器
        AdapterFactory.register_adapter("custom", CustomAdapter)

        # 验证注册成功
        adapters = AdapterFactory.list_available_adapters()
        assert "custom" in adapters

        # 创建自定义适配器实例
        adapter = AdapterFactory.create("custom", {})
        assert adapter is not None
        assert isinstance(adapter, CustomAdapter)
