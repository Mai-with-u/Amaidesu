"""
StickerOutputProvider 测试
"""

import base64
import io
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from src.domains.output.providers.sticker import StickerOutputProvider
from src.modules.di.context import ProviderContext
from src.modules.types import ActionType, Intent, IntentAction


@pytest.fixture
def mock_provider_context():
    """Mock ProviderContext for testing"""
    return ProviderContext(
        event_bus=MagicMock(),
        config_service=MagicMock(),
    )


@pytest.fixture
def sticker_config():
    """Sticker配置"""
    return {
        "sticker_size": 0.5,
        "sticker_rotation": 90,
        "sticker_position_x": 0.5,
        "sticker_position_y": -0.3,
        "image_width": 128,
        "image_height": 128,
        "cool_down_seconds": 3,
        "display_duration_seconds": 5,
    }


@pytest.fixture
def sample_image_base64():
    """创建一个简单的测试图片base64"""
    # 创建一个简单的红色图片
    img = Image.new("RGB", (256, 256), color="red")
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


@pytest.fixture
def mock_event_bus():
    """Mock EventBus"""
    event_bus = MagicMock()
    event_bus.on = MagicMock()
    event_bus.emit = AsyncMock()
    return event_bus


@pytest.fixture
def sample_intent_with_sticker(sample_image_base64):
    """创建带有 STICKER 动作的 Intent"""
    return Intent(
        original_text="测试贴纸",
        response_text="这是测试回复",
        actions=[
            IntentAction(
                type=ActionType.STICKER,
                params={"sticker_image": sample_image_base64},
            )
        ],
    )


@pytest.fixture
def sample_intent_without_sticker():
    """创建没有 STICKER 动作的 Intent"""
    return Intent(
        original_text="测试文本",
        response_text="这是测试回复",
        actions=[],
    )


@pytest.mark.skip(reason="需要外部环境 (VTS Provider)")
class TestStickerOutputProvider:
    """测试 StickerOutputProvider"""

    def test_init_with_default_config(self, mock_provider_context):
        """测试默认配置初始化"""
        provider = StickerOutputProvider({}, context=mock_provider_context)

        assert provider.sticker_size == 0.33
        assert provider.sticker_rotation == 90
        assert provider.sticker_position_x == 0
        assert provider.sticker_position_y == 0
        assert provider.image_width == 256
        assert provider.image_height == 256
        assert provider.cool_down_seconds == 5
        assert provider.display_duration_seconds == 3

    def test_init_with_custom_config(self, sticker_config, mock_provider_context):
        """测试自定义配置初始化"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)

        assert provider.sticker_size == 0.5
        assert provider.sticker_rotation == 90
        assert provider.sticker_position_x == 0.5
        assert provider.sticker_position_y == -0.3
        assert provider.image_width == 128
        assert provider.image_height == 128
        assert provider.cool_down_seconds == 3
        assert provider.display_duration_seconds == 5

    def test_resize_image_base64_with_both_dimensions(self, sticker_config, sample_image_base64, mock_provider_context):
        """测试同时设置宽高的图片调整"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)

        resized = provider._resize_image_base64(sample_image_base64)

        # 验证返回的是base64字符串
        assert isinstance(resized, str)
        # 解码并检查尺寸
        image_data = base64.b64decode(resized)
        img = Image.open(io.BytesIO(image_data))
        assert img.size == (128, 128)

    def test_resize_image_base64_with_width_only(self, sticker_config, sample_image_base64, mock_provider_context):
        """测试只设置宽度的图片调整"""
        config = sticker_config.copy()
        config["image_height"] = 0  # 只设置宽度

        provider = StickerOutputProvider(config, context=mock_provider_context)

        resized = provider._resize_image_base64(sample_image_base64)

        # 解码并检查尺寸
        image_data = base64.b64decode(resized)
        img = Image.open(io.BytesIO(image_data))
        assert img.size[0] == 128  # 宽度应该是128
        assert img.size[1] > 0  # 高度应该按比例计算

    def test_resize_image_base64_with_height_only(self, sticker_config, sample_image_base64, mock_provider_context):
        """测试只设置高度的图片调整"""
        config = sticker_config.copy()
        config["image_width"] = 0  # 只设置高度

        provider = StickerOutputProvider(config, context=mock_provider_context)

        resized = provider._resize_image_base64(sample_image_base64)

        # 解码并检查尺寸
        image_data = base64.b64decode(resized)
        img = Image.open(io.BytesIO(image_data))
        assert img.size[1] == 128  # 高度应该是128
        assert img.size[0] > 0  # 宽度应该按比例计算

    def test_resize_image_base64_no_resize(self, sample_image_base64, mock_provider_context):
        """测试不调整大小"""
        config = {"image_width": 0, "image_height": 0}
        provider = StickerOutputProvider(config, context=mock_provider_context)

        resized = provider._resize_image_base64(sample_image_base64)

        # 应该返回原始base64
        assert resized == sample_image_base64

    def test_resize_image_base64_invalid_base64(self, sticker_config, mock_provider_context):
        """测试无效的base64输入"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)

        # 无效的base64应该返回原始输入
        invalid_base64 = "not_a_valid_base64!!!"
        resized = provider._resize_image_base64(invalid_base64)

        assert resized == invalid_base64

    @pytest.mark.asyncio
    async def test_init(self, sticker_config, mock_provider_context):
        """测试初始化逻辑"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)
        # _find_vts_provider 返回 None，所以不会有 VTS Provider
        await provider.init()
        # 不应该抛出异常

    @pytest.mark.asyncio
    async def test_execute_in_cooldown(self, sticker_config, sample_intent_with_sticker, mock_provider_context):
        """测试冷却期内的执行"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)

        # 设置最近触发时间
        provider.last_trigger_time = time.monotonic()

        # 尝试执行
        await provider.execute(sample_intent_with_sticker)

        # 由于在冷却期内，send_to_vts不应该被调用
        # 我们可以验证last_trigger_time没有更新太多

    @pytest.mark.asyncio
    async def test_execute_without_sticker_action(self, sticker_config, sample_intent_without_sticker, mock_provider_context):
        """测试没有 STICKER 动作的执行"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)

        # 确保不在冷却期
        provider.last_trigger_time = 0

        # Mock _send_to_vts
        provider._send_to_vts = AsyncMock()

        # 尝试执行
        await provider.execute(sample_intent_without_sticker)

        # send_to_vts不应该被调用
        provider._send_to_vts.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_success(self, sticker_config, sample_intent_with_sticker, mock_provider_context):
        """测试成功的执行"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)

        # Mock VTS Provider
        provider._vts_provider = AsyncMock()
        provider._vts_provider.load_item = AsyncMock(return_value="test_instance_id")
        provider._vts_provider.unload_item = AsyncMock(return_value=True)

        # 确保不在冷却期
        provider.last_trigger_time = 0

        # 执行
        await provider.execute(sample_intent_with_sticker)

        # 验证load_item被调用
        provider._vts_provider.load_item.assert_called_once()
        # 验证冷却时间已更新
        assert provider.last_trigger_time > 0

    @pytest.mark.asyncio
    async def test_send_to_vts(self, sticker_config, sample_image_base64, mock_provider_context):
        """测试发送到VTS"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)

        # Mock VTS Provider
        mock_vts = AsyncMock()
        mock_vts.load_item = AsyncMock(return_value="test_instance_id")
        mock_vts.unload_item = AsyncMock(return_value=True)
        provider._vts_provider = mock_vts

        # 发送到VTS
        await provider._send_to_vts(sample_image_base64)

        # 验证load_item被调用
        mock_vts.load_item.assert_called_once()
        # 验证参数正确
        call_args = mock_vts.load_item.call_args
        assert call_args[1]["custom_data_base64"] == sample_image_base64
        assert call_args[1]["size"] == 0.5
        assert call_args[1]["rotation"] == 90

    @pytest.mark.asyncio
    async def test_cleanup(self, sticker_config, mock_provider_context):
        """测试清理资源"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)

        # 清理不应该抛出异常
        await provider.cleanup()

    @pytest.mark.asyncio
    async def test_full_execute_workflow(self, sticker_config, sample_intent_with_sticker, mock_provider_context):
        """测试完整的执行工作流"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)

        # Mock VTS Provider
        provider._vts_provider = AsyncMock()
        provider._vts_provider.load_item = AsyncMock(return_value="test_instance_id")
        provider._vts_provider.unload_item = AsyncMock(return_value=True)

        # 确保不在冷却期
        provider.last_trigger_time = 0

        # 执行
        await provider.execute(sample_intent_with_sticker)

        # 验证load_item被调用
        provider._vts_provider.load_item.assert_called_once()
        # 验证冷却时间已更新
        assert provider.last_trigger_time > 0

    @pytest.mark.asyncio
    async def test_execute_without_vts_provider(self, sticker_config, sample_intent_with_sticker, mock_provider_context):
        """测试没有 VTS Provider 时的执行"""
        provider = StickerOutputProvider(sticker_config, context=mock_provider_context)
        # _vts_provider 默认为 None

        # 确保不在冷却期
        provider.last_trigger_time = 0

        # Mock _send_to_vts
        provider._send_to_vts = AsyncMock()

        # 执行
        await provider.execute(sample_intent_with_sticker)

        # send_to_vts不应该被调用
        provider._send_to_vts.assert_not_called()
