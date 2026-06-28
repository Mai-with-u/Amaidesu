"""
MaiBotDecider._normalized_to_message_base 出站协议契约测试。

回归测试:H-2(time 字段毫秒→秒)+ H-3(additional_config 键名)修复。

这两个修复共同保证 Amaidesu 发送给 MaiBot 的 MessageBase 严格符合
MaiBot-v1.0.0 协议:
- `message_info.time` 单位 = 秒(MaiBot 端用 datetime.fromtimestamp() 解析)
- `additional_config` 用 v1.0.0 平台 IO 键名(platform_io_*)
"""

from unittest.mock import MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.types.base.normalized_message import NormalizedMessage
from src.stages.decision.deciders.maibot.maibot_decider import MaiBotDecider


# 固定毫秒值,便于精确断言时间戳换算
# 1729612345678 ms = 1729612345.678 s(2024-10-23 14:32:25.678 UTC)
FIXED_TIMESTAMP_MS = 1729612345678
FIXED_TIMESTAMP_SECONDS = 1729612345.678


@pytest.fixture
def decider():
    """构造一个最小可用的 MaiBotDecider 实例。"""
    return MaiBotDecider(
        config={"type": "maibot", "host": "127.0.0.1", "port": 8000},
        event_bus=MagicMock(spec=EventBus),
    )


@pytest.fixture
def fixed_normalized_message() -> NormalizedMessage:
    """构造一个时间戳固定的 NormalizedMessage,便于精确断言。"""
    return NormalizedMessage(
        text="测试消息",
        source="console_input",
        timestamp_ms=FIXED_TIMESTAMP_MS,
        user_id="user_test_001",
        user_nickname="tester",
    )


class TestOutboundTimeFieldSeconds:
    """H-2:出站 message_info.time 必须是秒(Unix epoch float),不是毫秒。"""

    def test_time_is_float_not_milliseconds(self, decider, fixed_normalized_message):
        """time 字段类型为 float,值不是毫秒级整数。"""
        result = decider._normalized_to_message_base(fixed_normalized_message)

        assert result is not None
        # 毫秒值是 13 位整数;秒值是 10 位浮点数,且必须为 float
        assert isinstance(result.message_info.time, float), (
            f"time 应为 float (秒),实际类型: {type(result.message_info.time).__name__},值: {result.message_info.time}"
        )

    def test_time_equals_milliseconds_divided_by_1000(self, decider, fixed_normalized_message):
        """time 值应精确等于 timestamp_ms / 1000.0。"""
        result = decider._normalized_to_message_base(fixed_normalized_message)

        assert result is not None
        assert result.message_info.time == pytest.approx(FIXED_TIMESTAMP_MS / 1000.0)

    def test_time_is_within_reasonable_seconds_range(self, decider, fixed_normalized_message):
        """防御性断言:time 值应在 2000-2100 年区间(防止再次误写为毫秒)。"""
        result = decider._normalized_to_message_base(fixed_normalized_message)

        assert result is not None
        # 当前时间的秒值约为 1.7e9;毫秒值约为 1.7e12
        # 上限 2.5e9 = 2049 年,下限 1e9 = 2001 年
        assert 1e9 < result.message_info.time < 2.5e9, (
            f"time 值 {result.message_info.time} 不在合理秒值范围 [1e9, 2.5e9] 内,疑似把毫秒当秒传了"
        )

    def test_time_not_equal_to_raw_milliseconds(self, decider, fixed_normalized_message):
        """反向断言:time 不应等于 timestamp_ms 本身(毫秒值)。"""
        result = decider._normalized_to_message_base(fixed_normalized_message)

        assert result is not None
        assert result.message_info.time != fixed_normalized_message.timestamp_ms


class TestOutboundAdditionalConfigKeys:
    """H-3:出站 additional_config 必须用 MaiBot-v1.0.0 的 platform_io_* 键名。"""

    def test_uses_platform_io_account_id_key(self, decider, fixed_normalized_message):
        """additional_config 应包含 platform_io_account_id(不是旧键 account_id)。"""
        result = decider._normalized_to_message_base(fixed_normalized_message)

        assert result is not None
        assert "platform_io_account_id" in result.message_info.additional_config
        assert result.message_info.additional_config["platform_io_account_id"] == "amaidesu"

    def test_uses_platform_io_scope_key(self, decider, fixed_normalized_message):
        """additional_config 应包含 platform_io_scope(不是旧键 scope)。"""
        result = decider._normalized_to_message_base(fixed_normalized_message)

        assert result is not None
        assert "platform_io_scope" in result.message_info.additional_config
        assert result.message_info.additional_config["platform_io_scope"] == "live_room"

    def test_does_not_use_legacy_account_id_key(self, decider, fixed_normalized_message):
        """反向断言:旧键 account_id 不应再出现在 additional_config。"""
        result = decider._normalized_to_message_base(fixed_normalized_message)

        assert result is not None
        assert "account_id" not in result.message_info.additional_config

    def test_does_not_use_legacy_scope_key(self, decider, fixed_normalized_message):
        """反向断言:旧键 scope 不应再出现在 additional_config。"""
        result = decider._normalized_to_message_base(fixed_normalized_message)

        assert result is not None
        assert "scope" not in result.message_info.additional_config

    def test_additional_config_preserves_other_keys(self, decider, fixed_normalized_message):
        """其他键(source / original_platform / original_message_id)应保持不变。"""
        result = decider._normalized_to_message_base(fixed_normalized_message)

        assert result is not None
        cfg = result.message_info.additional_config
        assert cfg["source"] == "amaidesu"
        assert cfg["original_platform"] == "console_input"
        assert cfg["original_message_id"] == f"normalized_{FIXED_TIMESTAMP_MS}"


class TestOutboundProtocolRoundTrip:
    """端到端验证:序列化后通过 maim_message.from_dict 能完整解析回 MessageBase。"""

    def test_serialized_message_round_trips_through_maim_message(self, decider, fixed_normalized_message):
        """MessageBase.to_dict() → MessageBase.from_dict() 应保持 time 字段语义不变。

        这一步验证 Amaidesu 端发出去的 dict 是 MaiBot-v1.0.0 端能正确解析的格式。
        """
        from maim_message import MessageBase

        result = decider._normalized_to_message_base(fixed_normalized_message)
        assert result is not None

        # 模拟 wire protocol: Amaidesu to_dict → MaiBot from_dict
        wire_dict = result.to_dict()
        restored = MessageBase.from_dict(wire_dict)

        # MaiBot 端会用 fromtimestamp(time) 解析;此处断言我们传出去的字段
        # 落在合理秒值范围,从而 MaiBot 端能正确还原为 datetime
        assert isinstance(restored.message_info.time, float)
        assert restored.message_info.time == pytest.approx(FIXED_TIMESTAMP_SECONDS)
        assert restored.message_info.additional_config["platform_io_account_id"] == "amaidesu"
        assert restored.message_info.additional_config["platform_io_scope"] == "live_room"
