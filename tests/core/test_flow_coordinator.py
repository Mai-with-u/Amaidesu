"""
FlowCoordinator 单元测试

测试 FlowCoordinator 的所有核心功能：
- 初始化和设置
- 启动/停止生命周期管理
- Intent 事件处理
- 清理和资源释放
- 依赖获取
- 错误处理

运行: uv run pytest tests/core/test_flow_coordinator.py -v
"""

import asyncio
from unittest.mock import AsyncMock
from typing import Dict, Any

import pytest

from src.core.flow_coordinator import FlowCoordinator
from src.core.event_bus import EventBus
from src.core.events.names import CoreEvents
from src.core.events.payloads import (
    MessageReadyPayload,
    IntentPayload,
    DecisionRequestPayload,
)
from src.domains.output.parameters.expression_generator import ExpressionGenerator
from src.domains.output.manager import OutputProviderManager
from src.domains.decision.intent import Intent, EmotionType, IntentAction, ActionType


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def event_bus():
    """创建 EventBus 实例"""
    return EventBus(enable_stats=True)


@pytest.fixture
def mock_expression_generator():
    """创建模拟的 ExpressionGenerator"""
    mock_gen = AsyncMock(spec=ExpressionGenerator)
    mock_gen.generate = AsyncMock()
    return mock_gen


@pytest.fixture
def mock_output_provider_manager():
    """创建模拟的 OutputProviderManager"""
    mock_mgr = AsyncMock(spec=OutputProviderManager)
    mock_mgr.load_from_config = AsyncMock()
    mock_mgr.setup_all_providers = AsyncMock()
    mock_mgr.stop_all_providers = AsyncMock()
    return mock_mgr


@pytest.fixture
def sample_config():
    """创建测试用的配置"""
    return {
        "expression_generator": {
            "default_tts_enabled": True,
            "default_subtitle_enabled": True,
        },
        "providers": {
            "output": {
                "enabled": True,
                "enabled_outputs": ["subtitle", "tts"],
                "outputs": {
                    "subtitle": {"type": "subtitle", "enabled": True},
                    "tts": {"type": "tts", "enabled": True},
                },
            }
        },
    }


@pytest.fixture
def sample_intent():
    """创建测试用的 Intent 对象"""
    return Intent(
        original_text="你好",
        response_text="你好！很高兴见到你！",
        emotion=EmotionType.HAPPY,
        actions=[
            IntentAction(type=ActionType.WAVE, params={"duration": 1.0}, priority=50),
        ],
        metadata={"source": "test"},
    )


@pytest.fixture
def flow_coordinator(
    event_bus: EventBus,
    mock_expression_generator: AsyncMock,
    mock_output_provider_manager: AsyncMock,
):
    """创建 FlowCoordinator 实例"""
    return FlowCoordinator(
        event_bus=event_bus,
        expression_generator=mock_expression_generator,
        output_provider_manager=mock_output_provider_manager,
    )


# =============================================================================
# 初始化和设置测试
# =============================================================================


@pytest.mark.asyncio
async def test_flow_coordinator_initialization(event_bus: EventBus):
    """测试 FlowCoordinator 初始化"""
    coordinator = FlowCoordinator(event_bus=event_bus)

    assert coordinator.event_bus == event_bus
    assert coordinator.expression_generator is None
    assert coordinator.output_provider_manager is None
    assert coordinator._is_setup is False
    assert coordinator._event_handler_registered is False


@pytest.mark.asyncio
async def test_flow_coordinator_initialization_with_dependencies(
    event_bus: EventBus,
    mock_expression_generator: AsyncMock,
    mock_output_provider_manager: AsyncMock,
):
    """测试带依赖注入的初始化"""
    coordinator = FlowCoordinator(
        event_bus=event_bus,
        expression_generator=mock_expression_generator,
        output_provider_manager=mock_output_provider_manager,
    )

    assert coordinator.expression_generator == mock_expression_generator
    assert coordinator.output_provider_manager == mock_output_provider_manager


@pytest.mark.asyncio
async def test_setup_creates_expression_generator_if_not_provided(
    event_bus: EventBus,
    sample_config: Dict[str, Any],
):
    """测试 setup 在未提供 ExpressionGenerator 时创建默认实例"""
    coordinator = FlowCoordinator(event_bus=event_bus)

    await coordinator.setup(sample_config)

    assert coordinator.expression_generator is not None
    assert isinstance(coordinator.expression_generator, ExpressionGenerator)
    assert coordinator._is_setup is True


@pytest.mark.asyncio
async def test_setup_creates_output_provider_manager_if_not_provided(
    event_bus: EventBus,
    sample_config: Dict[str, Any],
):
    """测试 setup 在未提供 OutputProviderManager 时创建默认实例"""
    coordinator = FlowCoordinator(event_bus=event_bus)

    await coordinator.setup(sample_config)

    assert coordinator.output_provider_manager is not None
    assert isinstance(coordinator.output_provider_manager, OutputProviderManager)
    assert coordinator._is_setup is True


@pytest.mark.asyncio
async def test_setup_loads_providers_from_config(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 setup 从配置加载 Provider"""
    await flow_coordinator.setup(sample_config)

    # 验证 load_from_config 被调用（包含config_service参数）
    flow_coordinator.output_provider_manager.load_from_config.assert_called_once_with(
        sample_config, core=None, config_service=None
    )


@pytest.mark.asyncio
async def test_setup_subscribes_to_intent_event(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
    event_bus: EventBus,
):
    """测试 setup 订阅 Intent 事件"""
    await flow_coordinator.setup(sample_config)

    assert flow_coordinator._event_handler_registered is True
    # 验证事件订阅
    assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT_GENERATED) == 1


@pytest.mark.asyncio
async def test_setup_with_existing_dependencies(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
    mock_expression_generator: AsyncMock,
    mock_output_provider_manager: AsyncMock,
):
    """测试使用已注入的依赖进行 setup"""
    await flow_coordinator.setup(sample_config)

    # 验证使用的是注入的依赖，而不是创建新的
    assert flow_coordinator.expression_generator == mock_expression_generator
    assert flow_coordinator.output_provider_manager == mock_output_provider_manager


@pytest.mark.asyncio
async def test_setup_can_be_called_multiple_times(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
    event_bus: EventBus,
):
    """测试 setup 可以被多次调用（但会重复订阅事件）"""
    await flow_coordinator.setup(sample_config)
    first_listener_count = event_bus.get_listeners_count(CoreEvents.DECISION_INTENT_GENERATED)

    await flow_coordinator.setup(sample_config)
    second_listener_count = event_bus.get_listeners_count(CoreEvents.DECISION_INTENT_GENERATED)

    # 当前实现：每次 setup 都会订阅事件（会重复）
    # 这是已知行为，需要在 FlowCoordinator 实现中添加防重复订阅逻辑
    assert first_listener_count == 1
    assert second_listener_count == 2  # 当前实现会重复订阅

    # cleanup 应该取消所有订阅
    await flow_coordinator.cleanup()
    final_count = event_bus.get_listeners_count(CoreEvents.DECISION_INTENT_GENERATED)
    # 注意：cleanup 只取消一次订阅，所以还有一个残留
    # 这证明了重复订阅的问题
    assert final_count == 1


# =============================================================================
# 启动/停止测试
# =============================================================================


@pytest.mark.asyncio
async def test_start_before_setup(flow_coordinator: FlowCoordinator):
    """测试在 setup 之前调用 start"""
    # 不应该抛出异常，但应该记录警告
    await flow_coordinator.start()
    # 如果没有 setup，start 不应该调用 provider 的方法
    flow_coordinator.output_provider_manager.setup_all_providers.assert_not_called()


@pytest.mark.asyncio
async def test_start_after_setup(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试在 setup 之后正常启动"""
    await flow_coordinator.setup(sample_config)
    await flow_coordinator.start()

    # 验证 setup_all_providers 被调用
    flow_coordinator.output_provider_manager.setup_all_providers.assert_called_once_with(
        flow_coordinator.event_bus
    )


@pytest.mark.asyncio
async def test_start_with_provider_failure(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 Provider 启动失败时的错误处理"""
    # 模拟启动失败
    flow_coordinator.output_provider_manager.setup_all_providers.side_effect = Exception(
        "Provider startup failed"
    )

    await flow_coordinator.setup(sample_config)

    # 不应该抛出异常，而是记录错误
    await flow_coordinator.start()


@pytest.mark.asyncio
async def test_stop(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试停止 FlowCoordinator"""
    await flow_coordinator.setup(sample_config)
    await flow_coordinator.stop()

    # 验证 stop_all_providers 被调用
    flow_coordinator.output_provider_manager.stop_all_providers.assert_called_once()


@pytest.mark.asyncio
async def test_stop_with_provider_failure(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 Provider 停止失败时的错误处理"""
    # 模拟停止失败
    flow_coordinator.output_provider_manager.stop_all_providers.side_effect = Exception(
        "Provider stop failed"
    )

    await flow_coordinator.setup(sample_config)

    # 不应该抛出异常，而是记录错误
    await flow_coordinator.stop()


@pytest.mark.asyncio
async def test_lifecycle_flow(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试完整的生命周期流程：setup → start → stop"""
    await flow_coordinator.setup(sample_config)
    assert flow_coordinator._is_setup is True

    await flow_coordinator.start()
    flow_coordinator.output_provider_manager.setup_all_providers.assert_called_once()

    await flow_coordinator.stop()
    flow_coordinator.output_provider_manager.stop_all_providers.assert_called_once()


# =============================================================================
# Intent 事件处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_on_intent_ready_generates_parameters(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
    sample_intent: Intent,
):
    """测试 Intent 事件处理生成 ExpressionParameters"""
    # 模拟 generate 返回值
    from src.domains.output.parameters.render_parameters import ExpressionParameters

    mock_params = ExpressionParameters(
        tts_text=sample_intent.response_text,
        subtitle_text=sample_intent.response_text,
    )
    flow_coordinator.expression_generator.generate.return_value = mock_params

    await flow_coordinator.setup(sample_config)

    # 发布 Intent 事件
    await flow_coordinator.event_bus.emit(
        CoreEvents.DECISION_INTENT_GENERATED,
        IntentPayload.from_intent(sample_intent, provider="DecisionProvider"),
        source="DecisionProvider",
    )

    await asyncio.sleep(0.1)  # 等待异步处理

    # 验证 generate 被调用
    flow_coordinator.expression_generator.generate.assert_called_once_with(sample_intent)


@pytest.mark.asyncio
async def test_on_intent_ready_emits_parameters_event(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
    sample_intent: Intent,
):
    """测试 Intent 处理后发布 expression.parameters_generated 事件"""
    from src.domains.output.parameters.render_parameters import ExpressionParameters

    mock_params = ExpressionParameters(
        tts_text=sample_intent.response_text,
        subtitle_text=sample_intent.response_text,
    )
    flow_coordinator.expression_generator.generate.return_value = mock_params

    await flow_coordinator.setup(sample_config)

    # 监听 expression.parameters_generated 事件
    received_params = []
    async def on_params_generated(event_name, data, source):
        received_params.append(data)

    flow_coordinator.event_bus.on(
        CoreEvents.EXPRESSION_PARAMETERS_GENERATED, on_params_generated
    )

    # 发布 Intent 事件
    await flow_coordinator.event_bus.emit(
        CoreEvents.DECISION_INTENT_GENERATED,
        IntentPayload.from_intent(sample_intent, provider="DecisionProvider"),
        source="DecisionProvider",
    )

    await asyncio.sleep(0.1)  # 等待异步处理

    # 验证参数事件被发布
    assert len(received_params) == 1
    # received_params[0] 是 dict（EventBus 自动转换），需要检查关键字段
    assert received_params[0]["tts_text"] == mock_params.tts_text
    assert received_params[0]["subtitle_text"] == mock_params.subtitle_text


@pytest.mark.asyncio
async def test_on_intent_ready_missing_intent(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 Intent 事件数据中缺少必要字段"""
    await flow_coordinator.setup(sample_config)

    # 发布缺少必要字段的 IntentPayload
    # 使用 IntentPayload 但不包含必要的信息（比如空字符串）
    from src.core.events.payloads.decision import IntentPayload

    incomplete_payload = IntentPayload(
        original_text="",
        response_text="",
        emotion="neutral",
        provider="test",
    )

    await flow_coordinator.event_bus.emit(
        CoreEvents.DECISION_INTENT_GENERATED,
        incomplete_payload,
        source="DecisionProvider",
    )

    await asyncio.sleep(0.1)  # 等待异步处理

    # 验证 generate 仍然会被调用（因为 FlowCoordinator 会尝试处理所有 IntentPayload）
    # 但生成的 ExpressionParameters 可能是空的
    flow_coordinator.expression_generator.generate.assert_called_once()


@pytest.mark.asyncio
async def test_on_intent_ready_no_expression_generator(
    event_bus: EventBus,
    sample_config: Dict[str, Any],
    sample_intent: Intent,
):
    """测试 ExpressionGenerator 未初始化时的处理"""
    # 创建不带 ExpressionGenerator 的 coordinator
    coordinator = FlowCoordinator(event_bus=event_bus)
    await coordinator.setup(sample_config)

    # 发布 Intent 事件
    await event_bus.emit(
        CoreEvents.DECISION_INTENT_GENERATED,
        IntentPayload.from_intent(sample_intent, provider="DecisionProvider"),
        source="DecisionProvider",
    )

    await asyncio.sleep(0.1)  # 等待异步处理

    # 应该正常执行，不抛出异常


@pytest.mark.asyncio
async def test_on_intent_ready_with_exception(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
    sample_intent: Intent,
):
    """测试 Intent 处理过程中的异常处理"""
    # 模拟 generate 抛出异常
    flow_coordinator.expression_generator.generate.side_effect = Exception("Generation failed")

    await flow_coordinator.setup(sample_config)

    # 应该不抛出异常，而是记录错误
    await flow_coordinator.event_bus.emit(
        CoreEvents.DECISION_INTENT_GENERATED,
        IntentPayload.from_intent(sample_intent, provider="DecisionProvider"),
        source="DecisionProvider",
    )

    await asyncio.sleep(0.1)  # 等待异步处理


@pytest.mark.asyncio
async def test_on_intent_ready_data_flow(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
    sample_intent: Intent,
):
    """测试完整的数据流：Intent → ExpressionParameters → Event"""
    from src.domains.output.parameters.render_parameters import ExpressionParameters

    # 设置返回值
    mock_params = ExpressionParameters(
        tts_text=sample_intent.response_text,
        subtitle_text=sample_intent.response_text,
        expressions={"happy": 1.0},
    )
    flow_coordinator.expression_generator.generate.return_value = mock_params

    await flow_coordinator.setup(sample_config)

    # 记录事件发布
    emitted_events = []
    async def track_events(event_name, data, source):
        emitted_events.append({"name": event_name, "data": data, "source": source})

    flow_coordinator.event_bus.on(
        CoreEvents.EXPRESSION_PARAMETERS_GENERATED, track_events
    )

    # 触发数据流
    await flow_coordinator.event_bus.emit(
        CoreEvents.DECISION_INTENT_GENERATED,
        IntentPayload.from_intent(sample_intent, provider="DecisionProvider"),
        source="DecisionProvider",
    )

    await asyncio.sleep(0.1)

    # 验证完整数据流
    assert len(emitted_events) == 1
    assert emitted_events[0]["name"] == CoreEvents.EXPRESSION_PARAMETERS_GENERATED
    assert emitted_events[0]["source"] == "FlowCoordinator"
    # emitted_events[0]["data"] 是 dict（EventBus 自动转换），需要检查关键字段
    assert emitted_events[0]["data"]["tts_text"] == mock_params.tts_text
    assert emitted_events[0]["data"]["subtitle_text"] == mock_params.subtitle_text
    assert emitted_events[0]["data"]["expressions"] == mock_params.expressions


# =============================================================================
# 清理测试
# =============================================================================


@pytest.mark.asyncio
async def test_cleanup_unsubscribes_event_handler(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
    event_bus: EventBus,
):
    """测试 cleanup 取消事件订阅"""
    await flow_coordinator.setup(sample_config)

    # 验证事件已订阅
    assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT_GENERATED) == 1

    await flow_coordinator.cleanup()

    # 验证事件已取消订阅
    assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT_GENERATED) == 0
    assert flow_coordinator._event_handler_registered is False


@pytest.mark.asyncio
async def test_cleanup_resets_setup_flag(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 cleanup 重置 setup 标志"""
    await flow_coordinator.setup(sample_config)
    assert flow_coordinator._is_setup is True

    await flow_coordinator.cleanup()

    assert flow_coordinator._is_setup is False


@pytest.mark.asyncio
async def test_cleanup_without_setup(flow_coordinator: FlowCoordinator):
    """测试在 setup 之前调用 cleanup"""
    # 不应该抛出异常
    await flow_coordinator.cleanup()
    assert flow_coordinator._event_handler_registered is False


@pytest.mark.asyncio
async def test_cleanup_idempotent(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 cleanup 可以被多次调用"""
    await flow_coordinator.setup(sample_config)

    await flow_coordinator.cleanup()
    await flow_coordinator.cleanup()  # 第二次调用

    assert flow_coordinator._is_setup is False


@pytest.mark.asyncio
async def test_full_lifecycle_with_cleanup(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试完整的生命周期包括 cleanup"""
    # setup
    await flow_coordinator.setup(sample_config)
    assert flow_coordinator._is_setup is True

    # start
    await flow_coordinator.start()
    flow_coordinator.output_provider_manager.setup_all_providers.assert_called_once()

    # stop
    await flow_coordinator.stop()
    flow_coordinator.output_provider_manager.stop_all_providers.assert_called_once()

    # cleanup
    await flow_coordinator.cleanup()
    assert flow_coordinator._is_setup is False
    assert flow_coordinator._event_handler_registered is False


# =============================================================================
# 依赖获取测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_expression_generator(
    flow_coordinator: FlowCoordinator,
    mock_expression_generator: AsyncMock,
):
    """测试获取 ExpressionGenerator"""
    result = flow_coordinator.get_expression_generator()

    assert result == mock_expression_generator


@pytest.mark.asyncio
async def test_get_expression_generator_when_none(event_bus: EventBus):
    """测试获取未初始化的 ExpressionGenerator"""
    coordinator = FlowCoordinator(event_bus=event_bus)

    result = coordinator.get_expression_generator()

    assert result is None


@pytest.mark.asyncio
async def test_get_output_provider_manager(
    flow_coordinator: FlowCoordinator,
    mock_output_provider_manager: AsyncMock,
):
    """测试获取 OutputProviderManager"""
    result = flow_coordinator.get_output_provider_manager()

    assert result == mock_output_provider_manager


@pytest.mark.asyncio
async def test_get_output_provider_manager_when_none(event_bus: EventBus):
    """测试获取未初始化的 OutputProviderManager"""
    coordinator = FlowCoordinator(event_bus=event_bus)

    result = coordinator.get_output_provider_manager()

    assert result is None


# =============================================================================
# 集成测试
# =============================================================================


@pytest.mark.asyncio
async def test_full_integration_with_real_dependencies(
    event_bus: EventBus,
    sample_config: Dict[str, Any],
    sample_intent: Intent,
):
    """测试使用真实依赖的完整集成"""
    # 创建真实的依赖
    coordinator = FlowCoordinator(event_bus=event_bus)
    await coordinator.setup(sample_config)

    # 监听输出事件
    received_params = []
    async def on_params(event_name, data, source):
        received_params.append(data)

    event_bus.on(CoreEvents.EXPRESSION_PARAMETERS_GENERATED, on_params)

    # 启动
    await coordinator.start()

    # 发布 Intent 事件
    await event_bus.emit(
        CoreEvents.DECISION_INTENT_GENERATED,
        IntentPayload.from_intent(sample_intent, provider="DecisionProvider"),
        source="DecisionProvider",
    )

    await asyncio.sleep(0.2)  # 等待处理完成

    # 验证参数生成
    assert len(received_params) == 1
    # received_params[0] 是 dict（EventBus 自动转换）
    assert received_params[0]["tts_text"] == sample_intent.response_text
    assert received_params[0]["subtitle_text"] == sample_intent.response_text

    # 清理
    await coordinator.stop()
    await coordinator.cleanup()


@pytest.mark.asyncio
async def test_multiple_intents_sequential(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试顺序处理多个 Intent"""
    from src.domains.output.parameters.render_parameters import ExpressionParameters

    await flow_coordinator.setup(sample_config)

    # 监听输出事件
    received_count = []
    async def on_params(event_name, data, source):
        received_count.append(1)

    flow_coordinator.event_bus.on(
        CoreEvents.EXPRESSION_PARAMETERS_GENERATED, on_params
    )

    # 模拟 generate 返回
    flow_coordinator.expression_generator.generate.return_value = ExpressionParameters()

    # 发布多个 Intent
    for i in range(3):
        intent = Intent(
            original_text=f"测试{i}",
            response_text=f"回复{i}",
            emotion=EmotionType.HAPPY,
            actions=[],
            metadata={},
        )
        await flow_coordinator.event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            IntentPayload.from_intent(intent, provider="DecisionProvider"),
            source="DecisionProvider",
        )

    await asyncio.sleep(0.2)

    # 验证所有 Intent 都被处理
    assert len(received_count) == 3


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_setup_with_empty_config(event_bus: EventBus):
    """测试使用空配置进行 setup"""
    coordinator = FlowCoordinator(event_bus=event_bus)

    await coordinator.setup({})

    assert coordinator._is_setup is True
    assert coordinator.expression_generator is not None
    assert coordinator.output_provider_manager is not None


@pytest.mark.asyncio
async def test_setup_with_none_expression_generator_config(
    event_bus: EventBus,
):
    """测试 expression_generator 配置为 None"""
    coordinator = FlowCoordinator(event_bus=event_bus)

    config = {"expression_generator": None}
    await coordinator.setup(config)

    assert coordinator.expression_generator is not None


@pytest.mark.asyncio
async def test_intent_event_with_none_data(
    flow_coordinator: FlowCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 Intent 事件数据为 None"""
    await flow_coordinator.setup(sample_config)

    # 不应该抛出异常
    # 注意：EventBus 现在强制要求 Pydantic BaseModel，不再支持 None
    # 这个测试现在验证 FlowCoordinator 能正确处理 Payload
    # 但由于 Payload 是强类型的，None 会在 EventBus 层被拒绝
    # 所以这个测试现在只是验证系统不会崩溃

    # 由于 FlowCoordinator 订阅的是 CoreEvents.DECISION_INTENT_GENERATED
    # 它期望接收 IntentPayload，所以我们需要发送一个有效的 Payload
    # 但不包含实际的处理逻辑（比如 response_text 为空）

    from src.core.events.payloads.decision import IntentPayload

    empty_payload = IntentPayload(
        original_text="",
        response_text="",
        emotion="neutral",
        provider="test",
    )

    # 发送空 Payload - FlowCoordinator 应该能处理而不崩溃
    await flow_coordinator.event_bus.emit(
        CoreEvents.DECISION_INTENT_GENERATED,
        empty_payload,
        source="DecisionProvider",
    )

    await asyncio.sleep(0.1)


# =============================================================================
# 运行入口
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
