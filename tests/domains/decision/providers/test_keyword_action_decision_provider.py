"""
KeywordActionDecisionProvider 测试
"""

import pytest
from src.domains.decision.providers.keyword_action import KeywordActionDecisionProvider
from src.core.base.normalized_message import NormalizedMessage


@pytest.fixture
def keyword_action_config():
    """KeywordAction 配置"""
    return {
        "type": "keyword_action",
        "global_cooldown": 1.0,
        "default_response": "触发成功",
        "actions": [
            {
                "name": "微笑动作",
                "enabled": True,
                "keywords": ["微笑", "smile"],
                "match_mode": "anywhere",
                "cooldown": 2.0,
                "action_type": "hotkey",
                "action_params": {"key": "smile"},
                "priority": 50,
            },
            {
                "name": "打招呼",
                "enabled": True,
                "keywords": ["你好", "hello", "hi"],
                "match_mode": "exact",
                "cooldown": 5.0,
                "action_type": "expression",
                "action_params": {"name": "smile"},
                "priority": 80,
            },
        ],
    }


@pytest.fixture
def sample_message():
    """创建示例消息"""
    return NormalizedMessage(
        text="你好",
        content="你好",
        source="console",
        data_type="text",
        importance=0.5,
        user_id="test_user",
        metadata={"user_nickname": "测试用户"},
    )


class TestKeywordActionDecisionProvider:
    """测试 KeywordActionDecisionProvider"""

    def test_init_with_config(self, keyword_action_config):
        """测试初始化"""
        provider = KeywordActionDecisionProvider(keyword_action_config)

        assert len(provider.actions) == 2
        assert provider.global_cooldown == 1.0
        assert provider.default_response == "触发成功"

    def test_init_with_empty_config(self):
        """测试空配置初始化"""
        provider = KeywordActionDecisionProvider({})

        assert len(provider.actions) == 0
        assert provider.global_cooldown == 1.0

    @pytest.mark.asyncio
    async def test_decide_with_exact_match(self, keyword_action_config, sample_message):
        """测试精确匹配"""
        provider = KeywordActionDecisionProvider(keyword_action_config)

        sample_message.text = "你好"
        sample_message.content = "你好"
        intent = await provider.decide(sample_message)

        assert intent.original_text == "你好"
        assert len(intent.actions) == 1
        assert intent.actions[0].type == "expression"
        assert provider.match_count == 1

    @pytest.mark.asyncio
    async def test_decide_with_anywhere_match(self, keyword_action_config, sample_message):
        """测试任意位置匹配"""
        provider = KeywordActionDecisionProvider(keyword_action_config)

        sample_message.text = "请微笑一下"
        sample_message.content = "请微笑一下"
        intent = await provider.decide(sample_message)

        assert len(intent.actions) == 1
        assert intent.actions[0].type == "hotkey"
        assert provider.match_count == 1

    @pytest.mark.asyncio
    async def test_decide_no_match(self, keyword_action_config, sample_message):
        """测试无匹配"""
        provider = KeywordActionDecisionProvider(keyword_action_config)

        sample_message.text = "随机文本"
        sample_message.content = "随机文本"
        intent = await provider.decide(sample_message)

        assert len(intent.actions) == 0

    @pytest.mark.asyncio
    async def test_decide_with_cooldown(self, keyword_action_config, sample_message):
        """测试冷却时间"""
        provider = KeywordActionDecisionProvider(keyword_action_config)

        sample_message.text = "你好"
        sample_message.content = "你好"
        intent1 = await provider.decide(sample_message)
        assert len(intent1.actions) == 1

        # 立即再次触发（应该在冷却中）
        intent2 = await provider.decide(sample_message)
        assert len(intent2.actions) == 0
        assert provider.cooldown_skip_count == 1

    @pytest.mark.asyncio
    async def test_cleanup(self, keyword_action_config):
        """测试清理"""
        provider = KeywordActionDecisionProvider(keyword_action_config)
        provider.match_count = 10

        await provider.cleanup()

        assert provider.match_count == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
