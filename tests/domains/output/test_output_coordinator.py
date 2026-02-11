"""
OutputCoordinator 单元测试

测试 OutputCoordinator 的所有核心功能：
- 初始化和设置
- 启动/停止生命周期管理
- Intent 事件处理
- 清理和资源释放
- 依赖获取
- 错误处理

运行: uv run pytest tests/core/test_output_coordinator.py -v
"""

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.types import Intent
from src.domains.output import OutputCoordinator
from src.domains.output.provider_manager import OutputProviderManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    IntentPayload,
)
from src.modules.types import ActionType, EmotionType, IntentAction

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def event_bus():
    """创建 EventBus 实例"""
    return EventBus(enable_stats=True)


@pytest.fixture
def mock_output_provider_manager():
    """创建模拟的 OutputProviderManager"""
    mock_mgr = MagicMock(spec=OutputProviderManager)
    # 异步方法需要使用 AsyncMock
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
def output_coordinator(
    event_bus: EventBus,
    mock_output_provider_manager: MagicMock,
):
    """创建 OutputCoordinator 实例"""
    return OutputCoordinator(
        event_bus=event_bus,
        output_provider_manager=mock_output_provider_manager,
    )


# =============================================================================
# 初始化和设置测试
# =============================================================================


@pytest.mark.asyncio
async def test_flow_coordinator_initialization(event_bus: EventBus):
    """测试 FlowCoordinator 初始化"""
    coordinator = OutputCoordinator(event_bus=event_bus)

    assert coordinator.event_bus == event_bus
    # assert coordinator.expression_generator is None  # Removed: ExpressionGenerator deleted
    assert coordinator.output_provider_manager is None
    assert coordinator._is_setup is False
    assert coordinator._event_handler_registered is False


@pytest.mark.asyncio
async def test_flow_coordinator_initialization_with_dependencies(
    event_bus: EventBus,
    mock_output_provider_manager: MagicMock,
):
    """测试带依赖注入的初始化"""
    coordinator = OutputCoordinator(
        event_bus=event_bus,
        output_provider_manager=mock_output_provider_manager,
    )

    assert coordinator.output_provider_manager == mock_output_provider_manager


@pytest.mark.asyncio
async def test_setup_creates_output_provider_manager_if_not_provided(
    event_bus: EventBus,
    sample_config: Dict[str, Any],
):
    """测试 setup 在未提供 OutputProviderManager 时创建默认实例"""
    coordinator = OutputCoordinator(event_bus=event_bus)

    await coordinator.setup(sample_config)

    assert coordinator.output_provider_manager is not None
    assert isinstance(coordinator.output_provider_manager, OutputProviderManager)
    assert coordinator._is_setup is True


@pytest.mark.asyncio
async def test_setup_loads_providers_from_config(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 setup 从配置加载 Provider"""
    await output_coordinator.setup(sample_config)

    # 验证 load_from_config 被调用（包含config_service参数）
    output_coordinator.output_provider_manager.load_from_config.assert_called_once_with(
        sample_config, core=None, config_service=None
    )


@pytest.mark.asyncio
async def test_setup_subscribes_to_intent_event(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
    event_bus: EventBus,
):
    """测试 setup 订阅 Intent 事件"""
    await output_coordinator.setup(sample_config)

    assert output_coordinator._event_handler_registered is True
    # 验证事件订阅
    assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT) == 1


@pytest.mark.asyncio
async def test_setup_with_existing_dependencies(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
    mock_output_provider_manager: MagicMock,
):
    """测试使用已注入的依赖进行 setup"""
    await output_coordinator.setup(sample_config)

    # 验证使用的是注入的依赖，而不是创建新的
    assert output_coordinator.output_provider_manager == mock_output_provider_manager


@pytest.mark.asyncio
async def test_setup_can_be_called_multiple_times(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
    event_bus: EventBus,
):
    """测试 setup 可以被多次调用（但会重复订阅事件）"""
    await output_coordinator.setup(sample_config)
    first_listener_count = event_bus.get_listeners_count(CoreEvents.DECISION_INTENT)

    await output_coordinator.setup(sample_config)
    second_listener_count = event_bus.get_listeners_count(CoreEvents.DECISION_INTENT)

    # 当前实现：每次 setup 都会订阅事件（会重复）
    # 这是已知行为，需要在 OutputCoordinator 实现中添加防重复订阅逻辑
    assert first_listener_count == 1
    assert second_listener_count == 2  # 当前实现会重复订阅

    # cleanup 应该取消所有订阅
    await output_coordinator.cleanup()
    final_count = event_bus.get_listeners_count(CoreEvents.DECISION_INTENT)
    # 注意：cleanup 只取消一次订阅，所以还有一个残留
    # 这证明了重复订阅的问题
    assert final_count == 1


# =============================================================================
# 启动/停止测试
# =============================================================================


@pytest.mark.asyncio
async def test_start_before_setup(output_coordinator: OutputCoordinator):
    """测试在 setup 之前调用 start"""
    # 不应该抛出异常，但应该记录警告
    await output_coordinator.start()
    # 如果没有 setup，start 不应该调用 provider 的方法
    output_coordinator.output_provider_manager.setup_all_providers.assert_not_called()


@pytest.mark.asyncio
async def test_start_after_setup(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
):
    """测试在 setup 之后正常启动"""
    await output_coordinator.setup(sample_config)
    await output_coordinator.start()

    # 验证 setup_all_providers 被调用（注意：实际调用时会传递 dependencies 参数）
    output_coordinator.output_provider_manager.setup_all_providers.assert_called_once()
    # 验证调用时包含了正确的 event_bus 参数
    call_args = output_coordinator.output_provider_manager.setup_all_providers.call_args
    assert call_args[0][0] == output_coordinator.event_bus
    assert call_args[1]["dependencies"] == {}  # dependencies 参数应为空字典


@pytest.mark.asyncio
async def test_start_with_provider_failure(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 Provider 启动失败时的错误处理"""
    # 模拟启动失败
    output_coordinator.output_provider_manager.setup_all_providers.side_effect = Exception("Provider startup failed")

    await output_coordinator.setup(sample_config)

    # 不应该抛出异常，而是记录错误
    await output_coordinator.start()


@pytest.mark.asyncio
async def test_stop(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
):
    """测试停止 OutputCoordinator"""
    await output_coordinator.setup(sample_config)
    await output_coordinator.stop()

    # 验证 stop_all_providers 被调用
    output_coordinator.output_provider_manager.stop_all_providers.assert_called_once()


@pytest.mark.asyncio
async def test_stop_with_provider_failure(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 Provider 停止失败时的错误处理"""
    # 模拟停止失败
    output_coordinator.output_provider_manager.stop_all_providers.side_effect = Exception("Provider stop failed")

    await output_coordinator.setup(sample_config)

    # 不应该抛出异常，而是记录错误
    await output_coordinator.stop()


@pytest.mark.asyncio
async def test_lifecycle_flow(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
):
    """测试完整的生命周期流程：setup → start → stop"""
    await output_coordinator.setup(sample_config)
    assert output_coordinator._is_setup is True

    await output_coordinator.start()
    output_coordinator.output_provider_manager.setup_all_providers.assert_called_once()

    await output_coordinator.stop()
    output_coordinator.output_provider_manager.stop_all_providers.assert_called_once()


# =============================================================================
# Intent 事件处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_on_intent_ready_no_expression_generator(
    event_bus: EventBus,
    sample_config: Dict[str, Any],
    sample_intent: Intent,
):
    """测试 ExpressionGenerator 未初始化时的处理"""
    # 创建不带 ExpressionGenerator 的 coordinator
    coordinator = OutputCoordinator(event_bus=event_bus)
    await coordinator.setup(sample_config)

    # 发布 Intent 事件
    await event_bus.emit(
        CoreEvents.DECISION_INTENT,
        IntentPayload.from_intent(sample_intent, provider="DecisionProvider"),
        source="DecisionProvider",
    )

    await asyncio.sleep(0.1)  # 等待异步处理

    # 应该正常执行，不抛出异常


# =============================================================================
# 清理测试
# =============================================================================


@pytest.mark.asyncio
async def test_cleanup_unsubscribes_event_handler(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
    event_bus: EventBus,
):
    """测试 cleanup 取消事件订阅"""
    await output_coordinator.setup(sample_config)

    # 验证事件已订阅
    assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT) == 1

    await output_coordinator.cleanup()

    # 验证事件已取消订阅
    assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT) == 0
    assert output_coordinator._event_handler_registered is False


@pytest.mark.asyncio
async def test_cleanup_resets_setup_flag(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 cleanup 重置 setup 标志"""
    await output_coordinator.setup(sample_config)
    assert output_coordinator._is_setup is True

    await output_coordinator.cleanup()

    assert output_coordinator._is_setup is False


@pytest.mark.asyncio
async def test_cleanup_without_setup(output_coordinator: OutputCoordinator):
    """测试在 setup 之前调用 cleanup"""
    # 不应该抛出异常
    await output_coordinator.cleanup()
    assert output_coordinator._event_handler_registered is False


@pytest.mark.asyncio
async def test_cleanup_idempotent(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 cleanup 可以被多次调用"""
    await output_coordinator.setup(sample_config)

    await output_coordinator.cleanup()
    await output_coordinator.cleanup()  # 第二次调用

    assert output_coordinator._is_setup is False


@pytest.mark.asyncio
async def test_full_lifecycle_with_cleanup(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
):
    """测试完整的生命周期包括 cleanup"""
    # setup
    await output_coordinator.setup(sample_config)
    assert output_coordinator._is_setup is True

    # start
    await output_coordinator.start()
    output_coordinator.output_provider_manager.setup_all_providers.assert_called_once()

    # stop
    await output_coordinator.stop()
    output_coordinator.output_provider_manager.stop_all_providers.assert_called_once()

    # cleanup
    await output_coordinator.cleanup()
    assert output_coordinator._is_setup is False
    assert output_coordinator._event_handler_registered is False


# =============================================================================
# 依赖获取测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_expression_generator_when_none(event_bus: EventBus):
    """测试获取未初始化的 ExpressionGenerator - 已移除此方法"""
    coordinator = OutputCoordinator(event_bus=event_bus)

    # ExpressionGenerator 已被删除，此测试仅验证 coordinator 能正常初始化
    assert coordinator is not None


@pytest.mark.asyncio
async def test_get_output_provider_manager(
    output_coordinator: OutputCoordinator,
    mock_output_provider_manager: MagicMock,
):
    """测试获取 OutputProviderManager"""
    result = output_coordinator.get_output_provider_manager()

    assert result == mock_output_provider_manager


@pytest.mark.asyncio
async def test_get_output_provider_manager_when_none(event_bus: EventBus):
    """测试获取未初始化的 OutputProviderManager"""
    coordinator = OutputCoordinator(event_bus=event_bus)

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
    coordinator = OutputCoordinator(event_bus=event_bus)
    await coordinator.setup(sample_config)

    # 监听输出事件
    received_params = []

    async def on_params(event_name, data, source):
        received_params.append(data)

    event_bus.on(CoreEvents.OUTPUT_PARAMS, on_params)

    # 启动
    await coordinator.start()

    # 发布 Intent 事件
    await event_bus.emit(
        CoreEvents.DECISION_INTENT,
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


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_setup_with_empty_config(event_bus: EventBus):
    """测试使用空配置进行 setup"""
    coordinator = OutputCoordinator(event_bus=event_bus)

    await coordinator.setup({})

    assert coordinator._is_setup is True
    # assert coordinator.expression_generator is not None  # Removed: ExpressionGenerator deleted
    assert coordinator.output_provider_manager is not None


@pytest.mark.asyncio
async def test_setup_with_none_expression_generator_config(
    event_bus: EventBus,
):
    """测试 expression_generator 配置为 None"""
    coordinator = OutputCoordinator(event_bus=event_bus)

    config = {"expression_generator": None}
    await coordinator.setup(config)

    # assert coordinator.expression_generator is not None  # Removed: ExpressionGenerator deleted


@pytest.mark.asyncio
async def test_intent_event_with_none_data(
    output_coordinator: OutputCoordinator,
    sample_config: Dict[str, Any],
):
    """测试 Intent 事件数据为空"""
    await output_coordinator.setup(sample_config)

    # 不应该抛出异常
    # 注意：EventBus 现在强制要求 Pydantic BaseModel，不再支持 None
    # 这个测试现在验证 OutputCoordinator 能正确处理 Payload
    # 但由于 Payload 是强类型的，None 会在 EventBus 层被拒绝
    # 所以这个测试现在只是验证系统能处理空内容的 Payload

    # 由于 OutputCoordinator 订阅的是 CoreEvents.DECISION_INTENT
    # 它期望接收 IntentPayload，所以我们需要发送一个有效的 Payload
    # 但不包含实际的处理逻辑（比如 response_text 为空）

    from src.modules.events.payloads.decision import IntentPayload

    empty_payload = IntentPayload(
        intent_data={
            "original_text": "",
            "response_text": "",
            "emotion": "neutral",
            "actions": [],
            "metadata": {},
            "timestamp": 0,
        },
        provider="test",
    )

    # 发送空 Payload - OutputCoordinator 应该能处理而不崩溃
    await output_coordinator.event_bus.emit(
        CoreEvents.DECISION_INTENT,
        empty_payload,
        source="DecisionProvider",
    )

    await asyncio.sleep(0.1)


# =============================================================================
# 运行入口
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
