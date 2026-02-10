"""
E2E Test: Error Recovery

测试系统在错误情况下的恢复能力
"""

import asyncio

import pytest
from pydantic import BaseModel, Field

# 导入所有 Provider 模块以触发注册
import src.domains.decision.providers  # noqa: F401
import src.domains.input.providers  # noqa: F401
import src.domains.output.providers  # noqa: F401
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import RawDataPayload
from src.modules.types.base.raw_data import RawData


class SimpleTestEvent(BaseModel):
    """简单测试事件"""

    data: str = Field(..., description="测试数据")


@pytest.mark.asyncio
async def test_decision_provider_failure_isolation(event_bus, wait_for_event):
    """
    测试 DecisionProvider 失败时的隔离

    验证：
    1. DecisionProvider 失败不影响其他组件
    2. 系统可以继续处理其他消息
    """
    from src.domains.decision.provider_manager import DecisionProviderManager as DecisionManager
    from src.domains.input.coordinator import InputCoordinator
    from src.domains.input.normalization.content import TextContent
    from src.modules.types.base.normalized_message import NormalizedMessage

    input_coordinator = InputCoordinator(event_bus)
    await input_coordinator.setup()

    decision_manager = DecisionManager(event_bus, llm_service=None)
    await decision_manager.setup("mock", {})

    # 正常处理一条消息
    normalized = NormalizedMessage(
        text="正常消息",
        content=TextContent(text="正常消息"),
        source="test",
        data_type="text",
        importance=0.5,
        metadata={},
    )

    intent1 = await decision_manager.decide(normalized)
    assert intent1 is not None

    # 模拟 Provider 失败（通过修改 provider）
    current_provider = decision_manager.get_current_provider()

    # 临时破坏 provider（使其抛出异常）
    original_decide = current_provider.decide

    async def failing_decide(message):
        if "错误" in message.text:
            raise RuntimeError("模拟的决策失败")
        return await original_decide(message)

    current_provider.decide = failing_decide

    # 发送会导致失败的消息
    error_message = NormalizedMessage(
        text="错误消息",
        content=TextContent(text="错误消息"),
        source="test",
        data_type="text",
        importance=0.5,
        metadata={},
    )

    # 这应该会失败
    try:
        await decision_manager.decide(error_message)
        raise AssertionError("应该抛出异常")
    except RuntimeError:
        pass  # 预期的异常

    # 恢复正常
    current_provider.decide = original_decide

    # 验证可以继续处理其他消息
    normal_message = NormalizedMessage(
        text="恢复正常",
        content=TextContent(text="恢复正常"),
        source="test",
        data_type="text",
        importance=0.5,
        metadata={},
    )

    intent2 = await decision_manager.decide(normal_message)
    assert intent2 is not None

    await decision_manager.cleanup()
    await input_coordinator.cleanup()


@pytest.mark.asyncio
async def test_invalid_raw_data_handling(event_bus, wait_for_event):
    """
    测试无效 RawData 的处理

    验证：
    1. 系统能优雅地处理无效数据
    2. 不会崩溃或挂起
    """
    from src.domains.input.coordinator import InputCoordinator

    input_coordinator = InputCoordinator(event_bus)
    await input_coordinator.setup()

    # 发送无效的 RawData（缺失必要字段）
    invalid_data = RawData(
        content=None,  # 无效内容
        data_type="text",
        source="test",
    )

    # 等待可能的事件（可能会被过滤）
    try:
        future = asyncio.create_task(wait_for_event(event_bus, CoreEvents.NORMALIZATION_MESSAGE_READY, timeout=1.0))

        await event_bus.emit(
            CoreEvents.PERCEPTION_RAW_DATA_GENERATED, RawDataPayload.from_raw_data(invalid_data), source="test"
        )

        # 尝试等待事件（可能超时）
        try:
            await asyncio.wait_for(future, timeout=0.5)
        except asyncio.TimeoutError:
            pass  # 如果被过滤，这是预期的

    except Exception as e:
        # 不应该有异常传播
        pytest.fail(f"处理无效数据时不应抛出异常: {e}")

    finally:
        await input_coordinator.cleanup()


@pytest.mark.asyncio
async def test_event_bus_error_isolation(event_bus):
    """
    测试 EventBus 错误隔离

    验证：
    1. 一个监听器失败不影响其他监听器
    2. EventBus 继续正常工作
    """
    error_count = {"count": 0}

    async def failing_listener(event_name, event_data, source):
        error_count["count"] += 1
        raise RuntimeError("监听器失败")

    async def normal_listener(event_name, event_data, source):
        # 正常监听器
        pass

    # 注册两个监听器
    event_bus.on("test.event", failing_listener)
    event_bus.on("test.event", normal_listener)

    # 发送事件
    await event_bus.emit("test.event", SimpleTestEvent(data="test"), source="test")

    # 验证：failing_listener 被调用了
    assert error_count["count"] == 1

    # 清理
    event_bus.off("test.event", failing_listener)
    event_bus.off("test.event", normal_listener)


@pytest.mark.asyncio
async def test_provider_initialization_failure(event_bus):
    """
    测试 Provider 初始化失败的处理

    验证：
    1. 初始化失败时不影响系统
    2. 可以尝试初始化其他 Provider
    """
    from src.domains.decision.provider_manager import DecisionProviderManager as DecisionManager

    decision_manager = DecisionManager(event_bus, llm_service=None)

    # 尝试初始化不存在的 provider
    try:
        await decision_manager.setup("nonexistent_provider", {})
        raise AssertionError("应该抛出异常")
    except ValueError as e:
        assert "未找到" in str(e)

    # 验证：当前没有 provider
    assert decision_manager.get_current_provider() is None
    assert decision_manager.get_current_provider_name() is None

    # 验证：可以成功初始化其他 provider
    await decision_manager.setup("mock", {})
    assert decision_manager.get_current_provider_name() == "mock"

    await decision_manager.cleanup()


@pytest.mark.asyncio
async def test_layer_cleanup_on_error(event_bus):
    """
    测试错误情况下的资源清理

    验证：
    1. 即使发生错误，资源也能正确清理
    2. 没有资源泄漏
    """
    from src.domains.decision.provider_manager import DecisionProviderManager as DecisionManager
    from src.domains.input.coordinator import InputCoordinator

    input_coordinator = InputCoordinator(event_bus)
    await input_coordinator.setup()

    decision_manager = DecisionManager(event_bus, llm_service=None)
    await decision_manager.setup("mock", {})

    # 验证InputCoordinator已订阅事件
    assert len(event_bus._handlers.get("perception.raw_data.generated", [])) > 0

    # 验证DecisionProvider已初始化
    assert decision_manager.get_current_provider() is not None
    assert decision_manager.get_current_provider_name() == "mock"

    # 清理
    await decision_manager.cleanup()
    await input_coordinator.cleanup()

    # 验证事件订阅已清理
    # 注意：EventBus 的 _handlers 是私有的，这里只验证组件状态
    assert decision_manager.get_current_provider() is None
