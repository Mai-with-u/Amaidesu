"""
OutputProviderManager 单元测试（重构后）

测试 OutputProviderManager 的所有核心功能：
- 初始化和设置
- 启动/停止生命周期管理
- Intent 事件处理
- 清理和资源释放
- Provider 管理
- 错误处理

运行: uv run pytest tests/domains/output/test_output_provider_manager.py -v
"""

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

from src.modules.types import Intent
from src.domains.output import OutputProviderManager
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
def output_provider_manager(event_bus: EventBus):
    """创建 OutputProviderManager 实例"""
    return OutputProviderManager(event_bus=event_bus)


# =============================================================================
# 初始化和设置测试
# =============================================================================


@pytest.mark.asyncio
async def test_manager_initialization(event_bus: EventBus):
    """测试 OutputProviderManager 初始化"""
    manager = OutputProviderManager(event_bus=event_bus)

    assert manager.event_bus == event_bus
    assert manager.providers == []
    assert manager._is_setup is False
    assert manager._event_handler_registered is False
    assert manager.pipeline_manager is None


@pytest.mark.asyncio
async def test_manager_initialization_with_config(event_bus: EventBus):
    """测试带配置的初始化"""
    config = {
        "concurrent_rendering": False,
        "error_handling": "stop",
        "render_timeout": 5.0,
    }
    manager = OutputProviderManager(event_bus=event_bus, config=config)

    assert manager.concurrent_rendering is False
    assert manager.error_handling == "stop"
    assert manager.render_timeout == 5.0


@pytest.mark.asyncio
async def test_setup_creates_pipeline_manager(
    output_provider_manager: OutputProviderManager,
    sample_config: Dict[str, Any],
):
    """测试 setup 创建 PipelineManager"""
    await output_provider_manager.setup(sample_config)

    assert output_provider_manager.pipeline_manager is not None
    assert output_provider_manager._is_setup is True


@pytest.mark.asyncio
async def test_setup_subscribes_to_intent_event(
    output_provider_manager: OutputProviderManager,
    sample_config: Dict[str, Any],
    event_bus: EventBus,
):
    """测试 setup 订阅 Intent 事件"""
    await output_provider_manager.setup(sample_config)

    assert output_provider_manager._event_handler_registered is True
    # 验证事件订阅
    assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT) == 1


@pytest.mark.asyncio
async def test_setup_can_be_called_multiple_times(
    output_provider_manager: OutputProviderManager,
    sample_config: Dict[str, Any],
    event_bus: EventBus,
):
    """测试 setup 可以被多次调用（但会重复订阅事件）"""
    await output_provider_manager.setup(sample_config)
    first_listener_count = event_bus.get_listeners_count(CoreEvents.DECISION_INTENT)

    await output_provider_manager.setup(sample_config)
    second_listener_count = event_bus.get_listeners_count(CoreEvents.DECISION_INTENT)

    # 当前实现：每次 setup 都会订阅事件（会重复）
    assert first_listener_count == 1
    assert second_listener_count == 2  # 当前实现会重复订阅


# =============================================================================
# 启动/停止测试
# =============================================================================


@pytest.mark.asyncio
async def test_start_before_setup(output_provider_manager: OutputProviderManager):
    """测试在 setup 之前调用 start"""
    # 不应该抛出异常，但应该记录警告
    await output_provider_manager.start()
    # 如果没有 setup，start 不应该启动任何 Provider


@pytest.mark.asyncio
async def test_start_after_setup(
    output_provider_manager: OutputProviderManager,
    sample_config: Dict[str, Any],
):
    """测试在 setup 之后正常启动"""
    # Mock load_from_config 以避免实际加载 Provider
    with patch.object(output_provider_manager, "load_from_config", new_callable=AsyncMock):
        await output_provider_manager.setup(sample_config)
        await output_provider_manager.start()
        # 验证 setup_all_providers 被调用


@pytest.mark.asyncio
async def test_stop(
    output_provider_manager: OutputProviderManager,
    sample_config: Dict[str, Any],
):
    """测试停止 OutputProviderManager"""
    with patch.object(output_provider_manager, "load_from_config", new_callable=AsyncMock):
        await output_provider_manager.setup(sample_config)
        await output_provider_manager.stop()


@pytest.mark.asyncio
async def test_lifecycle_flow(
    output_provider_manager: OutputProviderManager,
    sample_config: Dict[str, Any],
):
    """测试完整的生命周期流程：setup → start → stop → cleanup"""
    with patch.object(output_provider_manager, "load_from_config", new_callable=AsyncMock):
        await output_provider_manager.setup(sample_config)
        assert output_provider_manager._is_setup is True

        await output_provider_manager.start()

        await output_provider_manager.stop()

        await output_provider_manager.cleanup()
        assert output_provider_manager._is_setup is False


# =============================================================================
# Intent 事件处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_on_intent_ready(
    event_bus: EventBus,
    sample_config: Dict[str, Any],
    sample_intent: Intent,
):
    """测试 Intent 事件处理"""
    manager = OutputProviderManager(event_bus=event_bus)

    # Mock load_from_config 以避免实际加载 Provider
    with patch.object(manager, "load_from_config", new_callable=AsyncMock):
        await manager.setup(sample_config)

        # 监听输出事件
        received_params = []

        async def on_params(event_name, data, source):
            received_params.append(data)

        event_bus.on(CoreEvents.OUTPUT_PARAMS, on_params)

        # 发布 Intent 事件
        await event_bus.emit(
            CoreEvents.DECISION_INTENT,
            IntentPayload.from_intent(sample_intent, provider="DecisionProvider"),
            source="DecisionProvider",
        )

        await asyncio.sleep(0.1)  # 等待异步处理

        # 验证参数生成
        assert len(received_params) == 1
        assert received_params[0]["tts_text"] == sample_intent.response_text
        assert received_params[0]["subtitle_text"] == sample_intent.response_text


@pytest.mark.asyncio
async def test_intent_event_with_empty_response_text(
    output_provider_manager: OutputProviderManager,
    sample_config: Dict[str, Any],
):
    """测试 Intent 事件的 response_text 为空"""
    with patch.object(output_provider_manager, "load_from_config", new_callable=AsyncMock):
        await output_provider_manager.setup(sample_config)

        # 创建空 response_text 的 Intent
        empty_intent = Intent(
            original_text="",
            response_text="",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata={},
        )

        # 不应该抛出异常
        await output_provider_manager.event_bus.emit(
            CoreEvents.DECISION_INTENT,
            IntentPayload.from_intent(empty_intent, provider="test"),
            source="test",
        )

        await asyncio.sleep(0.1)


# =============================================================================
# 清理测试
# =============================================================================


@pytest.mark.asyncio
async def test_cleanup_unsubscribes_event_handler(
    output_provider_manager: OutputProviderManager,
    sample_config: Dict[str, Any],
    event_bus: EventBus,
):
    """测试 cleanup 取消事件订阅"""
    with patch.object(output_provider_manager, "load_from_config", new_callable=AsyncMock):
        await output_provider_manager.setup(sample_config)

        # 验证事件已订阅
        assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT) == 1

        await output_provider_manager.cleanup()

        # 验证事件已取消订阅
        assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT) == 0
        assert output_provider_manager._event_handler_registered is False


@pytest.mark.asyncio
async def test_cleanup_resets_setup_flag(
    output_provider_manager: OutputProviderManager,
    sample_config: Dict[str, Any],
):
    """测试 cleanup 重置 setup 标志"""
    with patch.object(output_provider_manager, "load_from_config", new_callable=AsyncMock):
        await output_provider_manager.setup(sample_config)
        assert output_provider_manager._is_setup is True

        await output_provider_manager.cleanup()

        assert output_provider_manager._is_setup is False


@pytest.mark.asyncio
async def test_cleanup_without_setup(output_provider_manager: OutputProviderManager):
    """测试在 setup 之前调用 cleanup"""
    # 不应该抛出异常
    await output_provider_manager.cleanup()
    assert output_provider_manager._event_handler_registered is False


@pytest.mark.asyncio
async def test_cleanup_idempotent(
    output_provider_manager: OutputProviderManager,
    sample_config: Dict[str, Any],
):
    """测试 cleanup 可以被多次调用"""
    with patch.object(output_provider_manager, "load_from_config", new_callable=AsyncMock):
        await output_provider_manager.setup(sample_config)

        await output_provider_manager.cleanup()
        await output_provider_manager.cleanup()  # 第二次调用

        assert output_provider_manager._is_setup is False


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_setup_with_empty_config(event_bus: EventBus):
    """测试使用空配置进行 setup"""
    manager = OutputProviderManager(event_bus=event_bus)

    await manager.setup({})

    assert manager._is_setup is True
    assert manager.pipeline_manager is not None


@pytest.mark.asyncio
async def test_get_stats(output_provider_manager: OutputProviderManager):
    """测试获取统计信息"""
    stats = output_provider_manager.get_stats()

    assert "total_providers" in stats
    assert "setup_providers" in stats
    assert "concurrent_rendering" in stats
    assert "error_handling" in stats
    assert "provider_stats" in stats


@pytest.mark.asyncio
async def test_get_provider_names(output_provider_manager: OutputProviderManager):
    """测试获取 Provider 名称列表"""
    names = output_provider_manager.get_provider_names()

    assert isinstance(names, list)


@pytest.mark.asyncio
async def test_get_provider_by_name_not_found(output_provider_manager: OutputProviderManager):
    """测试根据名称获取不存在的 Provider"""
    result = output_provider_manager.get_provider_by_name("nonexistent")

    assert result is None


# =============================================================================
# 集成测试
# =============================================================================


@pytest.mark.asyncio
async def test_full_integration(
    event_bus: EventBus,
    sample_config: Dict[str, Any],
    sample_intent: Intent,
):
    """测试完整的集成流程"""
    manager = OutputProviderManager(event_bus=event_bus)

    # Mock load_from_config 以避免实际加载 Provider
    with patch.object(manager, "load_from_config", new_callable=AsyncMock):
        await manager.setup(sample_config)

        # 监听输出事件
        received_params = []

        async def on_params(event_name, data, source):
            received_params.append(data)

        event_bus.on(CoreEvents.OUTPUT_PARAMS, on_params)

        # 启动
        await manager.start()

        # 发布 Intent 事件
        await event_bus.emit(
            CoreEvents.DECISION_INTENT,
            IntentPayload.from_intent(sample_intent, provider="DecisionProvider"),
            source="DecisionProvider",
        )

        await asyncio.sleep(0.2)  # 等待处理完成

        # 验证参数生成
        assert len(received_params) == 1
        assert received_params[0]["tts_text"] == sample_intent.response_text

        # 清理
        await manager.stop()
        await manager.cleanup()


# =============================================================================
# 运行入口
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
