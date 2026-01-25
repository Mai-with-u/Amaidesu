"""
VTSProvider单元测试

由于VTSProvider依赖pyvts库和VTS Studio运行，这些测试主要验证：
1. Provider的初始化逻辑
2. 基本方法的逻辑正确性
3. 不依赖实际VTS连接的功能

需要实际VTS连接的测试标记为integration或skip
"""

import pytest
from unittest.mock import Mock, AsyncMock
from src.providers.vts_provider import VTSProvider
from src.expression.render_parameters import ExpressionParameters


@pytest.fixture
def mock_event_bus():
    """模拟EventBus"""
    event_bus = Mock()
    event_bus.emit = AsyncMock()
    event_bus.listen = Mock()
    event_bus.stop_listening = Mock()
    return event_bus


@pytest.fixture
def mock_core():
    """模拟AmaidesuCore"""
    core = Mock()
    core.get_service = Mock(return_value=None)
    return core


@pytest.fixture
def vts_config():
    """VTSProvider配置"""
    return {
        "vts_host": "localhost",
        "vts_port": 8001,
        "llm_matching_enabled": False,
        "lip_sync_enabled": True,
        "volume_threshold": 0.01,
        "smoothing_factor": 0.3,
        "vowel_detection_sensitivity": 0.5,
        "sample_rate": 32000,
    }


@pytest.fixture
def vts_provider(vts_config, mock_event_bus, mock_core):
    """VTSProvider实例"""
    return VTSProvider(vts_config, mock_event_bus, mock_core)


class TestVTSProviderInitialization:
    """测试VTSProvider初始化"""

    def test_init_with_config(self, vts_provider, vts_config):
        """测试配置初始化"""
        assert vts_provider.vts_host == vts_config["vts_host"]
        assert vts_provider.vts_port == vts_config["vts_port"]
        assert vts_provider.llm_matching_enabled == vts_config["llm_matching_enabled"]
        assert vts_provider.lip_sync_enabled == vts_config["lip_sync_enabled"]

    def test_init_default_config(self, mock_event_bus, mock_core):
        """测试默认配置"""
        provider = VTSProvider({}, mock_event_bus, mock_core)
        assert provider.vts_host == "localhost"
        assert provider.vts_port == 8001
        assert provider.llm_matching_enabled is False
        assert provider.lip_sync_enabled is True

    def test_init_with_llm_config(self, mock_event_bus, mock_core):
        """测试LLM配置初始化"""
        config = {
            "llm_matching_enabled": True,
            "llm_api_key": "test_key",
            "llm_base_url": "https://api.example.com",
            "llm_model": "deepseek-chat",
            "llm_temperature": 0.1,
            "llm_max_tokens": 100,
        }
        provider = VTSProvider(config, mock_event_bus, mock_core)
        assert provider.llm_matching_enabled is True
        assert provider.llm_api_key == "test_key"
        assert provider.llm_base_url == "https://api.example.com"
        assert provider.llm_model == "deepseek-chat"
        assert provider.llm_temperature == 0.1
        assert provider.llm_max_tokens == 100


class TestVTSProviderRender:
    """测试VTSProvider渲染逻辑"""

    @pytest.mark.asyncio
    async def test_render_with_expressions_enabled(self, vts_provider):
        """测试启用表情时的渲染"""
        # 模拟VTS连接
        vts_provider._is_connected_and_authenticated = True
        vts_provider.set_parameter_value = AsyncMock(return_value=True)

        # 创建测试参数
        params = ExpressionParameters(
            expressions_enabled=True,
            expressions={"MouthSmile": 0.8, "EyeOpenLeft": 0.9},
        )

        # 渲染
        await vts_provider._render_internal(params)

        # 验证
        assert vts_provider.set_parameter_value.call_count == 2
        vts_provider.set_parameter_value.assert_any_call("MouthSmile", 0.8)
        vts_provider.set_parameter_value.assert_any_call("EyeOpenLeft", 0.9)

    @pytest.mark.asyncio
    async def test_render_with_expressions_disabled(self, vts_provider):
        """测试禁用表情时的渲染"""
        vts_provider._is_connected_and_authenticated = True
        vts_provider.set_parameter_value = AsyncMock(return_value=True)

        params = ExpressionParameters(
            expressions_enabled=False,
            expressions={"MouthSmile": 0.8},
        )

        await vts_provider._render_internal(params)

        # 禁用时不应调用set_parameter_value
        assert vts_provider.set_parameter_value.call_count == 0

    @pytest.mark.asyncio
    async def test_render_with_hotkeys(self, vts_provider):
        """测试热键触发"""
        vts_provider._is_connected_and_authenticated = True
        vts_provider.trigger_hotkey = AsyncMock(return_value=True)

        params = ExpressionParameters(
            hotkeys_enabled=True,
            hotkeys=["Hotkey1", "Hotkey2"],
        )

        await vts_provider._render_internal(params)

        assert vts_provider.trigger_hotkey.call_count == 2
        vts_provider.trigger_hotkey.assert_any_call("Hotkey1")
        vts_provider.trigger_hotkey.assert_any_call("Hotkey2")

    @pytest.mark.asyncio
    async def test_render_with_hotkeys_disabled(self, vts_provider):
        """测试禁用热键时的渲染"""
        vts_provider._is_connected_and_authenticated = True
        vts_provider.trigger_hotkey = AsyncMock(return_value=True)

        params = ExpressionParameters(
            hotkeys_enabled=False,
            hotkeys=["Hotkey1"],
        )

        await vts_provider._render_internal(params)

        assert vts_provider.trigger_hotkey.call_count == 0


class TestVTSProviderMethods:
    """测试VTSProvider方法"""

    @pytest.mark.asyncio
    async def test_smile_method(self, vts_provider):
        """测试smile方法"""
        vts_provider._is_connected_and_authenticated = True
        vts_provider.set_parameter_value = AsyncMock(return_value=True)

        result = await vts_provider.smile(0.5)

        assert result is True
        vts_provider.set_parameter_value.assert_called_once_with("MouthSmile", 0.5)

    @pytest.mark.asyncio
    async def test_smile_method_not_connected(self, vts_provider):
        """测试未连接时的smile方法"""
        vts_provider._is_connected_and_authenticated = False

        result = await vts_provider.smile(0.5)

        assert result is False

    @pytest.mark.asyncio
    async def test_close_eyes_method(self, vts_provider):
        """测试close_eyes方法"""
        vts_provider._is_connected_and_authenticated = True
        vts_provider.set_parameter_value = AsyncMock(return_value=True)

        result = await vts_provider.close_eyes()

        assert result is True
        assert vts_provider.set_parameter_value.call_count == 2

    @pytest.mark.asyncio
    async def test_open_eyes_method(self, vts_provider):
        """测试open_eyes方法"""
        vts_provider._is_connected_and_authenticated = True
        vts_provider.set_parameter_value = AsyncMock(return_value=True)

        result = await vts_provider.open_eyes()

        assert result is True
        assert vts_provider.set_parameter_value.call_count == 2


class TestVTSProviderLipSync:
    """测试VTSProvider口型同步功能"""

    @pytest.mark.asyncio
    async def test_start_lip_sync_session(self, vts_provider):
        """测试启动口型同步会话"""
        vts_provider._is_connected_and_authenticated = True
        await vts_provider.start_lip_sync_session("测试文本")

        assert vts_provider.is_speaking is True
        assert vts_provider.current_volume == 0.0
        assert all(value == 0.0 for value in vts_provider.current_vowel_values.values())

    @pytest.mark.asyncio
    async def test_stop_lip_sync_session(self, vts_provider):
        """测试停止口型同步会话"""
        vts_provider._is_connected_and_authenticated = True
        vts_provider.set_parameter_value = AsyncMock(return_value=True)
        vts_provider.is_speaking = True

        await vts_provider.stop_lip_sync_session()

        assert vts_provider.is_speaking is False
        assert vts_provider.current_volume == 0.0
        # 验证口型参数被重置
        assert (
            vts_provider.set_parameter_value.call_count >= 6
        )  # Silence, Volume, MouthOpen, MouthSmile, EyeOpenLeft, EyeOpenRight + vowels

    @pytest.mark.asyncio
    async def test_check_audio_state_when_speaking(self, vts_provider):
        """测试说话时的音频状态检查"""
        vts_provider._is_connected_and_authenticated = True
        vts_provider.is_speaking = True

        # 应该不修改任何参数，只检查状态
        await vts_provider._check_audio_state()

        # 验证状态保持不变
        assert vts_provider.is_speaking is True

    @pytest.mark.asyncio
    async def test_check_audio_state_when_not_speaking(self, vts_provider):
        """测试不说话时的音频状态检查"""
        vts_provider._is_connected_and_authenticated = True
        vts_provider.set_parameter_value = AsyncMock(return_value=True)
        vts_provider.is_speaking = False

        await vts_provider._check_audio_state()

        # _check_audio_state现在不做任何操作，口型参数由start/stop_lip_sync_session管理
        assert vts_provider.set_parameter_value.call_count == 0


class TestVTSProviderStats:
    """测试VTSProvider统计信息"""

    def test_get_stats(self, vts_provider):
        """测试获取统计信息"""
        vts_provider.render_count = 10
        vts_provider.error_count = 2
        vts_provider.hotkey_list = [{"name": "Hotkey1"}, {"name": "Hotkey2"}]

        stats = vts_provider.get_stats()

        assert stats["name"] == "VTSProvider"
        assert stats["is_connected"] is False
        assert stats["render_count"] == 10
        assert stats["error_count"] == 2
        assert stats["hotkey_count"] == 2
        assert stats["lip_sync_enabled"] is True
        assert stats["llm_matching_enabled"] is False


class TestVTSProviderGetInfo:
    """测试VTSProvider get_info方法"""

    def test_get_info(self, vts_provider):
        """测试获取Provider信息"""
        vts_provider.is_setup = True

        info = vts_provider.get_info()

        assert info["name"] == "VTSProvider"
        assert info["is_setup"] is True
        assert info["type"] == "output_provider"


# ==================== 集成测试（需要VTS Studio运行）====================


@pytest.mark.integration
class TestVTSProviderIntegration:
    """VTSProvider集成测试（需要VTS Studio运行）"""

    @pytest.mark.asyncio
    async def test_setup_with_vts_connection(self, vts_config, mock_event_bus):
        """测试VTS连接设置（需要VTS Studio运行）"""
        pytest.skip("需要VTS Studio运行，手动测试")
        provider = VTSProvider(vts_config, mock_event_bus)
        # 实际测试需要VTS Studio运行
        await provider.setup(mock_event_bus)
        assert provider._is_connected_and_authenticated is True

    @pytest.mark.asyncio
    async def test_real_vts_rendering(self, vts_config, mock_event_bus):
        """测试真实的VTS渲染（需要VTS Studio运行）"""
        pytest.skip("需要VTS Studio运行，手动测试")
        provider = VTSProvider(vts_config, mock_event_bus)
        # 实际测试需要VTS Studio运行
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not integration"])
