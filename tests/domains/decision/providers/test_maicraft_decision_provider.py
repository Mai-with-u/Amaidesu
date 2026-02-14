"""
MaicraftDecisionProvider 测试
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.decision.providers.maicraft import MaicraftDecisionProvider
from src.modules.events.names import CoreEvents
from src.modules.types.base.normalized_message import NormalizedMessage


@pytest.fixture
def maicraft_config():
    return {
        "enabled": True,
        "factory_type": "log",
        "command_prefix": "!",
        "command_mappings": {
            "chat": "chat",
            "attack": "attack",
        },
    }


@pytest.fixture
def mock_event_bus():
    """Mock EventBus"""
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus


class TestMaicraftDecisionProvider:
    def test_init_with_config(self, maicraft_config):
        provider = MaicraftDecisionProvider(maicraft_config)
        assert provider.parsed_config.enabled is True
        assert provider.parsed_config.factory_type == "log"

    def test_init_with_disabled_config(self):
        config = {"enabled": False, "factory_type": "log"}
        provider = MaicraftDecisionProvider(config)
        assert provider.parsed_config.enabled is False

    @pytest.mark.asyncio
    async def test_init(self, maicraft_config):
        """测试 init 方法初始化动作工厂"""
        provider = MaicraftDecisionProvider(maicraft_config)
        provider.action_factory.initialize = AsyncMock(return_value=True)
        await provider.init()
        provider.action_factory.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup(self, maicraft_config):
        """测试 cleanup 方法清理动作工厂"""
        provider = MaicraftDecisionProvider(maicraft_config)
        provider.action_factory.cleanup = AsyncMock()
        await provider.cleanup()
        provider.action_factory.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_decide_with_chat_command(self, maicraft_config, mock_event_bus):
        """测试 decide 方法处理聊天命令，返回 None 并通过事件发布 Intent"""
        provider = MaicraftDecisionProvider(maicraft_config)
        await provider.start(event_bus=mock_event_bus)

        message = NormalizedMessage(
            text="!chat 你好", content="!chat 你好", source="test", data_type="text", importance=0.5, user_id="test"
        )
        result = await provider.decide(message)

        # 验证返回 None
        assert result is None

        # 验证事件已发布
        mock_event_bus.emit.assert_called_once()
        call_args = mock_event_bus.emit.call_args
        assert call_args[0][0] == CoreEvents.DECISION_INTENT

    @pytest.mark.asyncio
    async def test_decide_with_non_command(self, maicraft_config, mock_event_bus):
        """测试 decide 方法处理非命令消息，返回 None 且不发布事件"""
        provider = MaicraftDecisionProvider(maicraft_config)
        await provider.start(event_bus=mock_event_bus)

        message = NormalizedMessage(
            text="普通消息", content="普通消息", source="test", data_type="text", importance=0.5, user_id="test"
        )
        result = await provider.decide(message)

        # 验证返回 None
        assert result is None

        # 验证事件未被发布（非命令不发布事件）
        mock_event_bus.emit.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
