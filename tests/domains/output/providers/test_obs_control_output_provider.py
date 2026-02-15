"""
ObsControlOutputProvider 测试
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domains.output.providers.obs_control import ObsControlOutputProvider
from src.modules.di.context import ProviderContext
from src.modules.types import Intent, IntentAction, ActionType


@pytest.fixture
def mock_provider_context():
    """Mock ProviderContext for testing"""
    return ProviderContext(
        event_bus=MagicMock(),
        config_service=MagicMock(),
    )


@pytest.fixture
def obs_config():
    """OBS控制配置"""
    return {
        "host": "localhost",
        "port": 4455,
        "password": "test_password",
        "text_source_name": "text_source",
        "typewriter_enabled": False,
        "typewriter_speed": 0.1,
        "typewriter_delay": 0.5,
        "test_on_connect": False,  # 测试时不发送真实连接
    }


@pytest.fixture
def obs_config_with_typewriter():
    """启用逐字效果的配置"""
    config = {
        "host": "localhost",
        "port": 4455,
        "password": "test_password",
        "text_source_name": "text_source",
        "typewriter_enabled": True,
        "typewriter_speed": 0.05,
        "typewriter_delay": 0.3,
        "test_on_connect": False,
    }
    return config


@pytest.fixture
def mock_event_bus():
    """Mock EventBus"""
    event_bus = MagicMock()
    event_bus.on = MagicMock()
    event_bus.off = MagicMock()
    event_bus.emit = AsyncMock()
    return event_bus


@pytest.fixture
def mock_obs_client():
    """Mock OBS客户端"""
    mock_client = MagicMock()
    mock_client.set_input_settings = MagicMock()
    mock_client.set_current_program_scene = MagicMock()
    mock_client.set_source_enabled = MagicMock()
    mock_client.disconnect = MagicMock()
    return mock_client


@pytest.mark.skip(reason="需要外部环境 (OBS WebSocket)")
class TestObsControlOutputProvider:
    """测试 ObsControlOutputProvider"""

    def test_init_with_default_config(self, mock_provider_context):
        """测试默认配置初始化"""
        provider = ObsControlOutputProvider({}, context=mock_provider_context)

        assert provider.host == "localhost"
        assert provider.port == 4455
        assert provider.password is None
        assert provider.text_source_name == "text"
        assert provider.typewriter_enabled is False
        assert provider.typewriter_speed == 0.1
        assert provider.typewriter_delay == 0.5
        assert provider.test_on_connect is True
        assert provider.is_connected is False
        assert provider.obs_connection is None

    def test_init_with_custom_config(self, obs_config, mock_provider_context):
        """测试自定义配置初始化"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)

        assert provider.host == "localhost"
        assert provider.port == 4455
        assert provider.password == "test_password"
        assert provider.text_source_name == "text_source"
        assert provider.typewriter_enabled is False
        assert provider.typewriter_speed == 0.1
        assert provider.typewriter_delay == 0.5
        assert provider.test_on_connect is False

    def test_init_with_typewriter_config(self, obs_config_with_typewriter, mock_provider_context):
        """测试逐字效果配置初始化"""
        provider = ObsControlOutputProvider(obs_config_with_typewriter, context=mock_provider_context)

        assert provider.typewriter_enabled is True
        assert provider.typewriter_speed == 0.05
        assert provider.typewriter_delay == 0.3

    def test_init_with_invalid_config(self, mock_provider_context):
        """测试无效配置"""
        invalid_config = {
            "port": 99999,  # 超出范围
        }

        with pytest.raises(ValueError):
            ObsControlOutputProvider(invalid_config, context=mock_provider_context)

    @pytest.mark.asyncio
    async def testinit_without_obs_library(self, obs_config, mock_event_bus, mock_provider_context):
        """测试没有obsws-python库时的设置"""
        with patch("src.domains.output.providers.obs_control.obs_control_provider.obs", None):
            provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)

            with pytest.raises(RuntimeError, match="obsws-python库未安装"):
                await provider.start(mock_event_bus)

    @pytest.mark.asyncio
    async def testinit_success(self, obs_config, mock_event_bus, mock_obs_client, mock_provider_context):
        """测试成功设置"""
        with patch("src.domains.output.providers.obs_control.obs_control_provider.obs") as mock_obs:
            mock_obs.ReqClient.return_value = mock_obs_client

            provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
            await provider.start(mock_event_bus)

            # 验证OBS连接已创建
            mock_obs.ReqClient.assert_called_once_with(host="localhost", port=4455, password="test_password")

            # 验证事件监听器已注册 (3个OBS特有事件 + 1个OUTPUT_INTENT由基类订阅)
            assert mock_event_bus.on.call_count == 4

            # 验证注册的事件名称（包含OBS特有事件）
            call_args_list = [call[0][0] for call in mock_event_bus.on.call_args_list]
            assert "obs.send_text" in call_args_list
            assert "obs.switch_scene" in call_args_list
            assert "obs.set_source_visibility" in call_args_list

            # 验证连接状态
            assert provider.is_connected is True
            assert provider.obs_connection == mock_obs_client

    @pytest.mark.asyncio
    async def testinit_connection_failure(self, obs_config, mock_event_bus, mock_provider_context):
        """测试连接失败"""
        with patch("src.domains.output.providers.obs_control.obs_control_provider.obs") as mock_obs:
            mock_obs.ReqClient.side_effect = Exception("Connection failed")

            provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
            await provider.start(mock_event_bus)

            # 验证连接状态
            assert provider.is_connected is False
            assert provider.obs_connection is None

    @pytest.mark.asyncio
    async def testexecute_with_response_text(self, obs_config, mock_event_bus, mock_obs_client):
        """测试渲染响应文本"""
        with patch("src.domains.output.providers.obs_control.obs_control_provider.obs") as mock_obs:
            mock_obs.ReqClient.return_value = mock_obs_client

            provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
            await provider.start(mock_event_bus)

            # 创建 Intent
            intent = Intent(
                original_text="原始文本",
                response_text="测试回复文本",
            )

            # Mock _send_text_to_obs
            provider._send_text_to_obs = AsyncMock()

            # 执行渲染
            await provider.execute(intent)

            # 验证发送文本被调用
            provider._send_text_to_obs.assert_called_once_with("测试回复文本")

    @pytest.mark.asyncio
    async def testexecute_with_metadata_text(self, obs_config, mock_event_bus, mock_obs_client):
        """测试从元数据渲染文本"""
        with patch("src.domains.output.providers.obs_control.obs_control_provider.obs") as mock_obs:
            mock_obs.ReqClient.return_value = mock_obs_client

            provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
            await provider.start(mock_event_bus)

            # 创建 Intent（元数据包含文本）
            intent = Intent(
                original_text="原始文本",
                response_text="",  # 空响应
                metadata={"obs_text": "元数据文本"},
            )

            # Mock _send_text_to_obs
            provider._send_text_to_obs = AsyncMock()

            # 执行渲染
            await provider.execute(intent)

            # 验证发送文本被调用
            provider._send_text_to_obs.assert_called_once_with("元数据文本")

    @pytest.mark.asyncio
    async def testexecute_with_action_text(self, obs_config, mock_event_bus, mock_obs_client):
        """测试从动作渲染文本"""
        with patch("src.domains.output.providers.obs_control.obs_control_provider.obs") as mock_obs:
            mock_obs.ReqClient.return_value = mock_obs_client

            provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
            await provider.start(mock_event_bus)

            # 创建 Intent（动作包含文本）
            intent = Intent(
                original_text="原始文本",
                response_text="",
                actions=[
                    IntentAction(type=ActionType.CUSTOM, params={"text": "动作文本"}),
                ],
            )

            # Mock _send_text_to_obs
            provider._send_text_to_obs = AsyncMock()

            # 执行渲染
            await provider.execute(intent)

            # 验证发送文本被调用
            provider._send_text_to_obs.assert_called_once_with("动作文本")

    @pytest.mark.asyncio
    async def testexecute_without_text(self, obs_config, mock_event_bus, mock_obs_client):
        """测试没有文本时的渲染"""
        with patch("src.domains.output.providers.obs_control.obs_control_provider.obs") as mock_obs:
            mock_obs.ReqClient.return_value = mock_obs_client

            provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
            await provider.start(mock_event_bus)

            # 创建没有文本的 Intent
            intent = Intent(
                original_text="原始文本",
                response_text="",
            )

            # Mock _send_text_to_obs
            provider._send_text_to_obs = AsyncMock()

            # 执行渲染
            await provider.execute(intent)

            # 验证发送文本未被调用
            provider._send_text_to_obs.assert_not_called()

    @pytest.mark.asyncio
    async def testexecute_not_connected(self, obs_config, mock_event_bus):
        """测试未连接时的渲染"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.is_connected = False
        provider.obs_connection = None

        # 创建 Intent
        intent = Intent(
            original_text="原始文本",
            response_text="测试",
        )

        # Mock logger
        provider.logger.warning = MagicMock()

        # 执行渲染（不应抛出异常）
        await provider.execute(intent)

        # 验证警告日志
        provider.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_text_source(self, obs_config, mock_obs_client):
        """测试设置文本源"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.obs_connection = mock_obs_client

        await provider._set_text_source("测试文本")

        # 验证调用
        mock_obs_client.set_input_settings.assert_called_once_with("text_source", {"text": "测试文本"}, True)

    @pytest.mark.asyncio
    async def test_set_text_source_not_connected(self, obs_config):
        """测试未连接时设置文本源"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.obs_connection = None

        with pytest.raises(RuntimeError, match="OBS未连接"):
            await provider._set_text_source("测试文本")

    @pytest.mark.asyncio
    async def test_send_typewriter_effect(self, obs_config_with_typewriter, mock_obs_client):
        """测试逐字显示效果"""
        provider = ObsControlOutputProvider(obs_config_with_typewriter, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.typewriter_speed = 0.01  # 加快测试速度
        provider.typewriter_delay = 0.0

        text = "ABC"
        await provider._send_typewriter_effect(text)

        # 验证调用次数：清空 + A + B + C = 4次
        assert mock_obs_client.set_input_settings.call_count == 4

        # 验证调用顺序
        calls = mock_obs_client.set_input_settings.call_args_list
        assert calls[0][0][1]["text"] == ""  # 清空
        assert calls[1][0][1]["text"] == "A"
        assert calls[2][0][1]["text"] == "AB"
        assert calls[3][0][1]["text"] == "ABC"

    @pytest.mark.asyncio
    async def test_send_typewriter_effect_with_delay(self, obs_config_with_typewriter, mock_obs_client):
        """测试带延迟的逐字显示效果"""
        provider = ObsControlOutputProvider(obs_config_with_typewriter, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.typewriter_speed = 0.01
        provider.typewriter_delay = 0.1

        text = "Test"
        start_time = asyncio.get_event_loop().time()
        await provider._send_typewriter_effect(text)
        elapsed = asyncio.get_event_loop().time() - start_time

        # 验证总时间 >= 速度 * 字符数 + 延迟
        expected_min_time = 0.01 * 4 + 0.1
        assert elapsed >= expected_min_time

    @pytest.mark.asyncio
    async def test_send_text_to_obs_with_typewriter(self, obs_config_with_typewriter, mock_obs_client):
        """测试使用逐字效果发送文本"""
        provider = ObsControlOutputProvider(obs_config_with_typewriter, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.is_connected = True
        provider.typewriter_speed = 0.01
        provider.typewriter_delay = 0.0

        result = await provider._send_text_to_obs("Test", typewriter=True)

        # 验证返回成功
        assert result is True

        # 验证逐字效果被调用
        assert mock_obs_client.set_input_settings.call_count > 1

    @pytest.mark.asyncio
    async def test_send_text_to_obs_without_typewriter(self, obs_config, mock_obs_client):
        """测试不使用逐字效果发送文本"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.is_connected = True

        result = await provider._send_text_to_obs("Test", typewriter=False)

        # 验证返回成功
        assert result is True

        # 验证只调用一次（直接设置）
        mock_obs_client.set_input_settings.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_text_to_obs_not_connected(self, obs_config):
        """测试未连接时发送文本"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.is_connected = False
        provider.obs_connection = None

        # Mock logger
        provider.logger.warning = MagicMock()

        result = await provider._send_text_to_obs("Test")

        # 验证返回失败
        assert result is False

        # 验证警告日志
        provider.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_scene_success(self, obs_config, mock_obs_client):
        """测试成功切换场景"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.is_connected = True

        result = await provider.switch_scene("新场景")

        # 验证返回成功
        assert result is True

        # 验证调用
        mock_obs_client.set_current_program_scene.assert_called_once_with("新场景")

    @pytest.mark.asyncio
    async def test_switch_scene_not_connected(self, obs_config):
        """测试未连接时切换场景"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.is_connected = False
        provider.obs_connection = None

        # Mock logger
        provider.logger.warning = MagicMock()

        result = await provider.switch_scene("新场景")

        # 验证返回失败
        assert result is False

        # 验证警告日志
        provider.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_scene_failure(self, obs_config, mock_obs_client):
        """测试切换场景失败"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.is_connected = True
        mock_obs_client.set_current_program_scene.side_effect = Exception("Scene not found")

        # Mock logger
        provider.logger.error = MagicMock()

        result = await provider.switch_scene("不存在的场景")

        # 验证返回失败
        assert result is False

        # 验证错误日志
        provider.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_source_visibility_success(self, obs_config, mock_obs_client):
        """测试成功设置源可见性"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.is_connected = True

        result = await provider.set_source_visibility("测试源", True)

        # 验证返回成功
        assert result is True

        # 验证调用
        mock_obs_client.set_source_enabled.assert_called_once_with("测试源", True)

    @pytest.mark.asyncio
    async def test_set_source_visibility_not_connected(self, obs_config):
        """测试未连接时设置源可见性"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.is_connected = False
        provider.obs_connection = None

        # Mock logger
        provider.logger.warning = MagicMock()

        result = await provider.set_source_visibility("测试源", False)

        # 验证返回失败
        assert result is False

        # 验证警告日志
        provider.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_send_text_event_valid(self, obs_config, mock_obs_client):
        """测试处理有效的发送文本事件"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.is_connected = True

        # Mock _send_text_to_obs
        provider._send_text_to_obs = AsyncMock(return_value=True)

        # 触发事件
        await provider._handle_send_text_event(
            "obs.send_text", {"text": "事件文本", "typewriter": False}, "test_source"
        )

        # 验证调用
        provider._send_text_to_obs.assert_called_once_with("事件文本", False)

    @pytest.mark.asyncio
    async def test_handle_send_text_event_invalid_data(self, obs_config):
        """测试处理无效数据的发送文本事件"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)

        # Mock logger
        provider.logger.warning = MagicMock()

        # 触发事件（无效数据类型）
        await provider._handle_send_text_event("obs.send_text", "invalid", "test_source")

        # 验证警告日志
        provider.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_send_text_event_empty_text(self, obs_config, mock_obs_client):
        """测试处理空文本的发送文本事件"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.is_connected = True

        # Mock _send_text_to_obs
        provider._send_text_to_obs = AsyncMock()

        # Mock logger
        provider.logger.warning = MagicMock()

        # 触发事件（空文本）
        await provider._handle_send_text_event("obs.send_text", {"text": ""}, "test_source")

        # 验证未调用发送
        provider._send_text_to_obs.assert_not_called()

        # 验证警告日志
        provider.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_switch_scene_event_valid(self, obs_config, mock_obs_client):
        """测试处理有效的切换场景事件"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.is_connected = True

        # Mock switch_scene
        provider.switch_scene = AsyncMock(return_value=True)

        # 触发事件
        await provider._handle_switch_scene_event("obs.switch_scene", {"scene_name": "目标场景"}, "test_source")

        # 验证调用
        provider.switch_scene.assert_called_once_with("目标场景")

    @pytest.mark.asyncio
    async def test_handle_switch_scene_event_invalid_data(self, obs_config):
        """测试处理无效数据的切换场景事件"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)

        # Mock logger
        provider.logger.warning = MagicMock()

        # 触发事件（无效数据类型）
        await provider._handle_switch_scene_event("obs.switch_scene", "invalid", "test_source")

        # 验证警告日志
        provider.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_set_source_visibility_event_valid(self, obs_config, mock_obs_client):
        """测试处理有效的设置源可见性事件"""
        provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
        provider.obs_connection = mock_obs_client
        provider.is_connected = True

        # Mock set_source_visibility
        provider.set_source_visibility = AsyncMock(return_value=True)

        # 触发事件
        await provider._handle_set_source_visibility_event(
            "obs.set_source_visibility", {"source_name": "测试源", "visible": True}, "test_source"
        )

        # 验证调用
        provider.set_source_visibility.assert_called_once_with("测试源", True)

    @pytest.mark.asyncio
    async def testcleanup(self, obs_config, mock_event_bus, mock_obs_client):
        """测试内部停止"""
        with patch("src.domains.output.providers.obs_control.obs_control_provider.obs") as mock_obs:
            mock_obs.ReqClient.return_value = mock_obs_client

            provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
            await provider.start(mock_event_bus)

            # 执行停止
            await provider.stop()

            # 验证取消事件监听（3个OBS特有事件 + 1个OUTPUT_INTENT由基类取消订阅）
            assert mock_event_bus.off.call_count == 4

            # 验证断开连接
            mock_obs_client.disconnect.assert_called_once()

            # 验证状态已重置
            assert provider.obs_connection is None
            assert provider.is_connected is False

    @pytest.mark.asyncio
    async def testcleanup_with_disconnect_error(self, obs_config, mock_event_bus, mock_obs_client):
        """测试断开连接出错时的停止"""
        with patch("src.domains.output.providers.obs_control.obs_control_provider.obs") as mock_obs:
            mock_obs.ReqClient.return_value = mock_obs_client
            mock_obs_client.disconnect.side_effect = Exception("Disconnect error")

            provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)
            await provider.start(mock_event_bus)

            # Mock logger
            provider.logger.error = MagicMock()

            # 执行停止（不应抛出异常）
            await provider.stop()

            # 验证错误日志
            provider.logger.error.assert_called_once()

            # 验证状态已重置
            assert provider.obs_connection is None
            assert provider.is_connected is False

    @pytest.mark.asyncio
    async def test_full_workflow(self, obs_config, mock_event_bus, mock_obs_client):
        """测试完整工作流"""
        with patch("src.domains.output.providers.obs_control.obs_control_provider.obs") as mock_obs:
            mock_obs.ReqClient.return_value = mock_obs_client

            provider = ObsControlOutputProvider(obs_config, context=mock_provider_context)

            # 1. 启动
            await provider.start(mock_event_bus)
            assert provider.is_connected is True

            # 2. 渲染
            intent = Intent(
                original_text="原始文本",
                response_text="完整测试",
            )
            provider._send_text_to_obs = AsyncMock()
            await provider.execute(intent)
            provider._send_text_to_obs.assert_called_once()

            # 3. 场景切换
            result = await provider.switch_scene("场景1")
            assert result is True

            # 4. 源可见性
            result = await provider.set_source_visibility("源1", True)
            assert result is True

            # 5. 停止
            await provider.stop()
            assert provider.is_connected is False
