"""
AvatarOutputProvider 测试
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.domains.output.providers.avatar.avatar_output_provider import AvatarOutputProvider
from src.domains.output.parameters.render_parameters import RenderParameters


@pytest.fixture
def avatar_config_vts():
    """Avatar VTS 配置"""
    return {
        "adapter_type": "vts",
        "vts_host": "127.0.0.1",
        "vts_port": 8001,
    }


@pytest.fixture
def avatar_config_vrchat():
    """Avatar VRChat 配置"""
    return {
        "adapter_type": "vrchat",
        "vrc_host": "127.0.0.1",
        "vrc_in_port": 9001,
        "vrc_out_port": 9000,
    }


@pytest.fixture
def mock_event_bus():
    """Mock EventBus"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()
    return event_bus


@pytest.fixture
def mock_adapter():
    """Mock PlatformAdapter"""
    adapter = MagicMock()
    adapter.is_connected = True
    adapter.set_parameters = AsyncMock(return_value=True)
    adapter.connect = AsyncMock(return_value=True)
    adapter.disconnect = AsyncMock(return_value=True)
    return adapter


class TestAvatarOutputProvider:
    """测试 AvatarOutputProvider"""

    def test_init_vts_adapter(self, avatar_config_vts):
        """测试使用 VTS 适配器初始化"""
        provider = AvatarOutputProvider(avatar_config_vts)

        assert provider.adapter_type == "vts"
        assert provider.adapter is None

    def test_init_vrchat_adapter(self, avatar_config_vrchat):
        """测试使用 VRChat 适配器初始化"""
        provider = AvatarOutputProvider(avatar_config_vrchat)

        assert provider.adapter_type == "vrchat"
        assert provider.adapter is None

    def test_init_default_adapter_type(self):
        """测试默认适配器类型"""
        provider = AvatarOutputProvider({})

        assert provider.adapter_type == "vts"

    @pytest.mark.asyncio
    async def test_setup_internal_vts(self, avatar_config_vts, mock_adapter):
        """测试初始化 VTS 适配器"""
        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vts)

            await provider._setup_internal()

            assert provider.adapter is not None
            mock_adapter.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_internal_vrchat(self, avatar_config_vrchat, mock_adapter):
        """测试初始化 VRChat 适配器"""
        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vrchat)

            await provider._setup_internal()

            assert provider.adapter is not None
            mock_adapter.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_internal_adapter_creation_failure(self, avatar_config_vts):
        """测试适配器创建失败"""
        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=None):
            provider = AvatarOutputProvider(avatar_config_vts)

            with pytest.raises(RuntimeError, match="无法创建 vts 适配器"):
                await provider._setup_internal()

    @pytest.mark.asyncio
    async def test_setup_internal_adapter_connection_failure(self, avatar_config_vts, mock_adapter):
        """测试适配器连接失败"""
        mock_adapter.connect = AsyncMock(return_value=False)

        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vts)

            with pytest.raises(RuntimeError, match="vts 适配器连接失败"):
                await provider._setup_internal()

    @pytest.mark.asyncio
    async def test_render_internal_with_expressions(self, avatar_config_vts, mock_adapter):
        """测试渲染表情参数"""
        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vts)
            await provider._setup_internal()

            # 创建渲染参数
            render_params = RenderParameters(
                expressions_enabled=True,
                expressions={"smile": 0.8, "eye_open": 0.5},
            )

            # 执行渲染
            await provider._render_internal(render_params)

            # 验证适配器的 set_parameters 被调用
            mock_adapter.set_parameters.assert_called_once_with({"smile": 0.8, "eye_open": 0.5})

    @pytest.mark.asyncio
    async def test_render_internal_expressions_disabled(self, avatar_config_vts, mock_adapter):
        """测试表情参数禁用时的渲染"""
        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vts)
            await provider._setup_internal()

            # 创建禁用表情的渲染参数
            render_params = RenderParameters(
                expressions_enabled=False,
                expressions={"smile": 0.8},
            )

            # 执行渲染
            await provider._render_internal(render_params)

            # 验证适配器的 set_parameters 未被调用
            mock_adapter.set_parameters.assert_not_called()

    @pytest.mark.asyncio
    async def test_render_internal_no_expressions(self, avatar_config_vts, mock_adapter):
        """测试没有表情参数时的渲染"""
        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vts)
            await provider._setup_internal()

            # 创建没有表情的渲染参数（使用空字典）
            render_params = RenderParameters(expressions_enabled=True, expressions={})

            # 执行渲染
            await provider._render_internal(render_params)

            # 验证适配器的 set_parameters 未被调用（因为空字典被视为 False）
            mock_adapter.set_parameters.assert_not_called()

    @pytest.mark.asyncio
    async def test_render_internal_adapter_not_connected(self, avatar_config_vts, mock_adapter):
        """测试适配器未连接时的渲染"""
        mock_adapter.is_connected = False

        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vts)
            await provider._setup_internal()

            # 创建渲染参数
            render_params = RenderParameters(
                expressions_enabled=True,
                expressions={"smile": 0.8},
            )

            # 执行渲染
            await provider._render_internal(render_params)

            # 验证适配器的 set_parameters 未被调用
            mock_adapter.set_parameters.assert_not_called()

    @pytest.mark.asyncio
    async def test_render_internal_adapter_none(self, avatar_config_vts):
        """测试适配器为 None 时的渲染"""
        provider = AvatarOutputProvider(avatar_config_vts)
        provider.adapter = None

        # 创建渲染参数
        render_params = RenderParameters(
            expressions_enabled=True,
            expressions={"smile": 0.8},
        )

        # 执行渲染（不应该抛出异常）
        await provider._render_internal(render_params)

    @pytest.mark.asyncio
    async def test_cleanup_internal(self, avatar_config_vts, mock_adapter):
        """测试清理"""
        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vts)
            await provider._setup_internal()

            # 执行清理
            await provider._cleanup_internal()

            # 验证适配器的 disconnect 被调用
            mock_adapter.disconnect.assert_called_once()
            assert provider.adapter is None

    @pytest.mark.asyncio
    async def test_cleanup_internal_no_adapter(self, avatar_config_vts):
        """测试没有适配器时的清理"""
        provider = AvatarOutputProvider(avatar_config_vts)
        provider.adapter = None

        # 执行清理（不应该抛出异常）
        await provider._cleanup_internal()

    def test_get_info(self, avatar_config_vts):
        """测试获取 Provider 信息"""
        provider = AvatarOutputProvider(avatar_config_vts)

        info = provider.get_info()

        assert info["adapter_type"] == "vts"
        assert info["is_connected"] is False

    def test_get_info_with_connected_adapter(self, avatar_config_vts, mock_adapter):
        """测试获取已连接适配器的信息"""
        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vts)
            # 模拟已连接的适配器
            provider.adapter = mock_adapter

            info = provider.get_info()

            assert info["adapter_type"] == "vts"
            assert info["is_connected"] is True

    @pytest.mark.asyncio
    async def test_full_workflow_vrchat(self, avatar_config_vrchat, mock_adapter):
        """测试完整的 VRChat 工作流"""
        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vrchat)

            # 初始化
            await provider._setup_internal()
            assert provider.adapter is not None

            # 渲染
            render_params = RenderParameters(
                expressions_enabled=True,
                expressions={"smile": 0.8, "eye_open": 0.5},
            )
            await provider._render_internal(render_params)
            mock_adapter.set_parameters.assert_called_once()

            # 清理
            await provider._cleanup_internal()
            mock_adapter.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_render_error_handling(self, avatar_config_vts, mock_adapter):
        """测试渲染时的错误处理"""
        # 使 set_parameters 抛出异常
        mock_adapter.set_parameters = AsyncMock(side_effect=Exception("Test error"))

        with patch("src.domains.output.adapter_factory.AdapterFactory.create", return_value=mock_adapter):
            provider = AvatarOutputProvider(avatar_config_vts)
            await provider._setup_internal()

            # 创建渲染参数
            render_params = RenderParameters(
                expressions_enabled=True,
                expressions={"smile": 0.8},
            )

            # 执行渲染（不应该抛出异常，应该捕获错误）
            await provider._render_internal(render_params)

            # 验证 set_parameters 被调用
            mock_adapter.set_parameters.assert_called_once()
