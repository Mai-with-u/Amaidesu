"""
DGLab 服务单元测试
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from src.services.dg_lab import DGLabConfig, DGLabService, WaveformPreset


def create_mock_response(status=200, text=""):
    """创建 mock HTTP 响应"""
    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.text = AsyncMock(return_value=text)

    class MockContextManager:
        async def __aenter__(self):
            return mock_response

        async def __aexit__(self, *args):
            pass

    return MockContextManager()


def create_mock_session():
    """创建 mock HTTP 会话"""
    mock_session = AsyncMock()
    mock_session.post = lambda *args, **kwargs: create_mock_response()
    return mock_session


class TestDGLabConfig:
    """配置测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = DGLabConfig()
        assert config.api_base_url == "http://127.0.0.1:8081"
        assert config.default_strength == 10
        assert config.default_waveform == WaveformPreset.BIG
        assert config.shock_duration_seconds == 2.0
        assert config.max_strength == 50
        assert config.enable_safety_limit is True

    def test_custom_config(self):
        """测试自定义配置"""
        config = DGLabConfig(
            api_base_url="http://192.168.1.100:8081",
            default_strength=20,
            default_waveform=WaveformPreset.SMALL,
            shock_duration_seconds=3.0,
            max_strength=100,
            enable_safety_limit=False,
        )
        assert config.api_base_url == "http://192.168.1.100:8081"
        assert config.default_strength == 20
        assert config.default_waveform == WaveformPreset.SMALL
        assert config.shock_duration_seconds == 3.0
        assert config.max_strength == 100
        assert config.enable_safety_limit is False

    def test_invalid_waveform(self):
        """测试无效波形"""
        with pytest.raises(ValueError, match="无效的波形预设"):
            DGLabConfig(default_waveform="invalid_waveform")

    def test_strength_out_of_range(self):
        """测试强度超出范围"""
        with pytest.raises(ValueError):
            DGLabConfig(default_strength=-1)

        with pytest.raises(ValueError):
            DGLabConfig(default_strength=201)

    def test_max_strength_out_of_range(self):
        """测试最大强度超出范围"""
        with pytest.raises(ValueError):
            DGLabConfig(max_strength=-1)

        with pytest.raises(ValueError):
            DGLabConfig(max_strength=201)


@pytest.mark.asyncio
class TestDGLabService:
    """服务测试"""

    @pytest.fixture
    def config(self):
        """测试配置"""
        return DGLabConfig(
            api_base_url="http://127.0.0.1:8081",
            default_strength=10,
            default_waveform=WaveformPreset.BIG,
            shock_duration_seconds=1.0,  # 短时间用于测试
            max_strength=50,
            enable_safety_limit=True,
        )

    @pytest.fixture
    def service(self, config):
        """创建服务实例"""
        return DGLabService(config)

    async def test_setup_without_aiohttp(self, service):
        """测试缺少 aiohttp 时的初始化"""
        with patch("src.services.dg_lab.service.aiohttp", None):
            with pytest.raises(RuntimeError, match="aiohttp 未安装"):
                await service.setup()

    async def test_setup_success(self, service):
        """测试成功初始化"""
        await service.setup()
        assert service.is_initialized is True
        assert service.is_ready() is True
        await service.cleanup()

    async def test_cleanup(self, service):
        """测试清理"""
        await service.setup()
        await service.cleanup()
        assert service.is_initialized is False
        assert service.is_ready() is False

    async def test_trigger_shock_without_setup(self, service):
        """测试未初始化时触发电击"""
        result = await service.trigger_shock()
        assert result is False

    async def test_trigger_shock_with_defaults(self, service):
        """测试使用默认参数触发电击"""
        await service.setup()

        # Mock HTTP 会话
        call_count = {"count": 0}

        def mock_post(*args, **kwargs):
            call_count["count"] += 1
            return create_mock_response()

        mock_session = AsyncMock()
        mock_session.post = mock_post
        service._http_session = mock_session

        # 触发电击
        result = await service.trigger_shock()
        assert result is True

        # 验证调用了正确的 API
        assert call_count["count"] == 6  # 4 个设置 + 2 个重置

        await service.cleanup()

    async def test_trigger_shock_with_custom_params(self, service):
        """测试使用自定义参数触发电击"""
        await service.setup()

        # Mock HTTP 会话
        mock_session = create_mock_session()
        service._http_session = mock_session

        # 触发电击（自定义参数）
        result = await service.trigger_shock(strength=20, waveform=WaveformPreset.SMALL, duration=0.5)
        assert result is True

        await service.cleanup()

    async def test_trigger_shock_safety_limit(self, service):
        """测试安全限制"""
        await service.setup()

        # Mock HTTP 会话并捕获请求
        captured_requests = []

        def mock_post(*args, **kwargs):
            captured_requests.append(kwargs.get("json", {}))
            return create_mock_response()

        mock_session = AsyncMock()
        mock_session.post = mock_post
        service._http_session = mock_session

        # 尝试超过最大强度的电击
        result = await service.trigger_shock(strength=100)  # max_strength=50
        assert result is True

        # 验证实际使用的强度被限制
        # 检查强度设置请求
        strength_requests = [r for r in captured_requests if "strength" in r]
        assert len(strength_requests) > 0
        assert strength_requests[0]["strength"] == 50  # 被限制到最大值

        await service.cleanup()

    async def test_trigger_shock_concurrent(self, service):
        """测试并发触发电击（应该被忽略）"""
        await service.setup()

        # Mock HTTP 会话
        mock_session = create_mock_session()
        service._http_session = mock_session

        # 并发触发电击
        task1 = asyncio.create_task(service.trigger_shock(duration=2.0))
        await asyncio.sleep(0.1)  # 等待第一个任务开始
        task2 = asyncio.create_task(service.trigger_shock())

        # 第二个应该立即返回 False（被忽略）
        result2 = await task2
        assert result2 is False

        # 等待第一个完成
        result1 = await task1
        assert result1 is True

        await service.cleanup()

    async def test_trigger_shock_api_failure(self, service):
        """测试 API 调用失败"""
        await service.setup()

        # Mock HTTP 会话（失败响应）
        def mock_post(*args, **kwargs):
            return create_mock_response(status=500, text="Internal Server Error")

        mock_session = AsyncMock()
        mock_session.post = mock_post
        service._http_session = mock_session

        # 触发电击应该失败
        result = await service.trigger_shock()
        assert result is False

        await service.cleanup()

    async def test_trigger_shock_invalid_waveform(self, service):
        """测试无效波形"""
        await service.setup()

        # 无效波形应该失败
        result = await service.trigger_shock(waveform="invalid")
        assert result is False

        await service.cleanup()

    def test_get_status(self, service):
        """测试获取状态"""
        status = service.get_status()
        assert status.is_running is False
        assert status.current_strength == 0
        assert status.current_waveform == ""

    def test_is_ready(self, service):
        """测试就绪状态"""
        assert service.is_ready() is False
