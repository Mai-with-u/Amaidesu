"""
MaicraftDecisionProvider 测试
"""

from unittest.mock import AsyncMock

import pytest

from src.domains.decision.providers.maicraft import MaicraftDecisionProvider
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
    async def test_setup_internal(self, maicraft_config):
        provider = MaicraftDecisionProvider(maicraft_config)
        provider.action_factory.initialize = AsyncMock(return_value=True)
        await provider._setup_internal()
        provider.action_factory.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_internal(self, maicraft_config):
        provider = MaicraftDecisionProvider(maicraft_config)
        provider.action_factory.cleanup = AsyncMock()
        await provider._cleanup_internal()
        provider.action_factory.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_decide_with_chat_command(self, maicraft_config):
        provider = MaicraftDecisionProvider(maicraft_config)
        message = NormalizedMessage(
            text="!chat 你好", content="!chat 你好", source="test", data_type="text", importance=0.5, user_id="test"
        )
        intent = await provider.decide(message)
        assert len(intent.actions) > 0

    @pytest.mark.asyncio
    async def test_decide_with_non_command(self, maicraft_config):
        provider = MaicraftDecisionProvider(maicraft_config)
        message = NormalizedMessage(
            text="普通消息", content="普通消息", source="test", data_type="text", importance=0.5, user_id="test"
        )
        intent = await provider.decide(message)
        assert intent.metadata.get("is_default") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
