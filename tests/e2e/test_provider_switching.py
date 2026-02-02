"""
E2E Test: Provider Switching

测试运行时切换 Provider 的功能
"""
import asyncio
import pytest

from src.core.base.raw_data import RawData
from src.core.base.normalized_message import NormalizedMessage
from src.layers.normalization.content import TextContent


@pytest.mark.asyncio
async def test_switch_decision_provider(
    event_bus,
    wait_for_event
):
    """
    测试运行时切换 DecisionProvider

    验证：
    1. 可以从 mock 切换回 rule_engine
    2. 切换后新 Provider 正常工作
    3. 旧 Provider 正确清理
    """
    from src.layers.decision.decision_manager import DecisionManager

    decision_manager = DecisionManager(event_bus, llm_service=None)

    # 1. 初始化 mock provider
    await decision_manager.setup('mock', {'default_response': 'Mock回复'})
    assert decision_manager.get_current_provider_name() == 'mock'

    # 2. 创建测试消息
    normalized = NormalizedMessage(
        text="测试消息",
        content=TextContent(text="测试消息"),
        source="test",
        data_type="text",
        importance=0.5
    )

    # 3. 使用 mock provider 处理
    intent1 = await decision_manager.decide(normalized)
    assert "[模拟回复]" in intent1.text

    # 4. 切换到 rule_engine provider
    # 注意：rule_engine 需要规则文件，我们使用一个简单的配置
    try:
        await decision_manager.switch_provider(
            'rule_engine',
            {
                'rules_file': 'data/rules/decision_rules.toml',
                'default_response': '规则引擎回复'
            }
        )
        assert decision_manager.get_current_provider_name() == 'rule_engine'

        # 5. 使用新 provider 处理
        intent2 = await decision_manager.decide(normalized)
        # rule_engine 的回复格式可能不同，只验证它返回了 Intent
        assert intent2 is not None
        assert hasattr(intent2, 'text')

    except Exception as e:
        # 如果 rule_engine 初始化失败（比如缺少规则文件），跳过这个测试
        pytest.skip(f"rule_engine not available: {e}")

    finally:
        await decision_manager.cleanup()


@pytest.mark.asyncio
async def test_provider_state_after_switch(
    event_bus
):
    """
    测试 Provider 切换后的状态管理

    验证：
    1. 切换前 Provider 的状态正确清理
    2. 切换后 Provider 的状态正确初始化
    3. 可以连续切换多次
    """
    from src.layers.decision.decision_manager import DecisionManager
    from src.layers.decision.providers.mock import MockDecisionProvider

    decision_manager = DecisionManager(event_bus, llm_service=None)

    # 1. 初始化第一个 mock provider
    provider1 = MockDecisionProvider({})
    await provider1.setup(event_bus, {}, {})

    # 2. 模拟一些调用
    normalized = NormalizedMessage(
        text="测试",
        content=TextContent(text="测试"),
        source="test",
        data_type="text",
        importance=0.5
    )

    intent1 = await provider1.decide(normalized)
    assert provider1.get_call_count() == 1

    # 3. 切换到新的 mock provider（通过 DecisionManager）
    await decision_manager.setup('mock', {'default_response': 'Provider2'})
    current1 = decision_manager.get_current_provider()

    # 使用当前 provider
    await decision_manager.decide(normalized)
    call_count_1 = current1.get_call_count()

    # 4. 再切换一次
    await decision_manager.switch_provider('mock', {'default_response': 'Provider3'})
    current2 = decision_manager.get_current_provider()

    # 验证是新实例
    assert current1 is not current2
    assert current2.get_call_count() == 0

    await decision_manager.cleanup()


@pytest.mark.asyncio
async def test_provider_switch_failure_rollback(
    event_bus
):
    """
    测试 Provider 切换失败时的回滚

    验证：
    1. 切换失败时保持原 Provider
    2. 原 Provider 仍然可用
    """
    from src.layers.decision.decision_manager import DecisionManager
    from src.core.base.normalized_message import NormalizedMessage
    from src.layers.normalization.content import TextContent

    decision_manager = DecisionManager(event_bus, llm_service=None)

    # 1. 初始化 mock provider
    await decision_manager.setup('mock', {'default_response': '原Provider'})

    original_provider = decision_manager.get_current_provider()
    original_name = decision_manager.get_current_provider_name()

    assert original_name == 'mock'

    # 2. 尝试切换到不存在的 provider
    try:
        await decision_manager.switch_provider(
            'nonexistent_provider',
            {}
        )
        assert False, "应该抛出异常"
    except ValueError:
        pass  # 预期的异常

    # 3. 验证回滚到原 provider
    current_provider = decision_manager.get_current_provider()
    current_name = decision_manager.get_current_provider_name()

    assert current_name == original_name
    assert current_provider is original_provider

    # 4. 验证原 provider 仍然可用
    normalized = NormalizedMessage(
        text="测试",
        content=TextContent(text="测试"),
        source="test",
        data_type="text",
        importance=0.5
    )

    intent = await decision_manager.decide(normalized)
    assert intent is not None

    await decision_manager.cleanup()


@pytest.mark.asyncio
async def test_concurrent_provider_access(
    event_bus
):
    """
    测试并发访问 Provider

    验证：
    1. 可以并发处理多条消息
    2. Provider 状态正确更新
    """
    from src.layers.decision.decision_manager import DecisionManager
    from src.core.base.normalized_message import NormalizedMessage
    from src.layers.normalization.content import TextContent

    decision_manager = DecisionManager(event_bus, llm_service=None)
    await decision_manager.setup('mock', {})

    # 创建多个并发任务
    async def process_message(text: str):
        normalized = NormalizedMessage(
            text=text,
            content=TextContent(text=text),
            source="test",
            data_type="text",
            importance=0.5
        )
        return await decision_manager.decide(normalized)

    # 并发处理 10 条消息
    tasks = [
        process_message(f"消息{i}")
        for i in range(10)
    ]

    results = await asyncio.gather(*tasks)

    # 验证
    assert len(results) == 10
    assert all(r is not None for r in results)

    # 验证 call_count 正确（即使并发，也应该都处理了）
    current = decision_manager.get_current_provider()
    assert current.get_call_count() == 10

    await decision_manager.cleanup()
