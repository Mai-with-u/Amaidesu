"""
VRChatProvider 单元测试

注意：大部分需要外部环境的测试已被删除。
本文件保留不需要外部环境的测试。
"""

from unittest.mock import MagicMock

import pytest

from src.domains.output.providers.avatar.vrchat.vrchat_provider import (
    VRChatProvider,
)
from src.modules.di.context import ProviderContext


# GESTURE_MAP 是 VRChatProvider 的类变量
GESTURE_MAP = VRChatProvider.GESTURE_MAP


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_event_bus():
    event_bus = MagicMock()
    event_bus.on = MagicMock()
    event_bus.off = MagicMock()
    return event_bus


@pytest.fixture
def mock_provider_context(mock_event_bus):
    """Mock ProviderContext for testing"""
    return ProviderContext(
        event_bus=mock_event_bus,
        config_service=MagicMock(),
    )


@pytest.fixture
def vrchat_config():
    return {"vrc_host": "127.0.0.1", "vrc_out_port": 9000}


@pytest.fixture
def mock_osc_client():
    """Mock OSC client"""
    client = MagicMock()
    client.send_message = MagicMock()
    return client


@pytest.fixture
def mock_logger():
    """Mock logger"""
    return MagicMock()


# =============================================================================
# Tests - 不需要外部环境
# =============================================================================


class TestVRChatProviderGestures:
    """测试手势映射"""

    def test_gesture_map_exists(self):
        """测试 GESTURE_MAP 存在且是字典类型"""
        assert hasattr(VRChatProvider, "GESTURE_MAP")
        assert isinstance(GESTURE_MAP, dict)

    def test_gesture_map_has_all_9_gestures(self):
        """测试所有 9 个手势都有映射"""
        required = [
            "Neutral",
            "Wave",
            "Peace",
            "ThumbsUp",
            "RocknRoll",
            "HandGun",
            "Point",
            "Victory",
            "Cross",
        ]
        for gesture in required:
            assert gesture in GESTURE_MAP, f"缺少手势映射: {gesture}"

    def test_gesture_map_neutral_value(self):
        """测试 Neutral 手势值为 0"""
        assert GESTURE_MAP["Neutral"] == 0

    def test_gesture_map_wave_value(self):
        """测试 Wave 手势值为 1"""
        assert GESTURE_MAP["Wave"] == 1

    def test_gesture_map_peace_value(self):
        """测试 Peace 手势值为 2"""
        assert GESTURE_MAP["Peace"] == 2

    def test_gesture_map_thumbs_up_value(self):
        """测试 ThumbsUp 手势值为 3"""
        assert GESTURE_MAP["ThumbsUp"] == 3

    def test_gesture_map_rocknroll_value(self):
        """测试 RocknRoll 手势值为 4"""
        assert GESTURE_MAP["RocknRoll"] == 4

    def test_gesture_map_handgun_value(self):
        """测试 HandGun 手势值为 5"""
        assert GESTURE_MAP["HandGun"] == 5

    def test_gesture_map_point_value(self):
        """测试 Point 手势值为 6"""
        assert GESTURE_MAP["Point"] == 6

    def test_gesture_map_victory_value(self):
        """测试 Victory 手势值为 7"""
        assert GESTURE_MAP["Victory"] == 7

    def test_gesture_map_cross_value(self):
        """测试 Cross 手势值为 8"""
        assert GESTURE_MAP["Cross"] == 8
