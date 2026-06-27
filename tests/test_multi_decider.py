"""
Multi-Decider 支持测试

测试 DeciderManager 的多 Decider 并行支持功能：
1. 多个 Decider 从配置加载
2. 每个 Decider 独立订阅 INPUT_MESSAGE_RECEIVED 事件
3. Speech 冲突警告机制
4. 向后兼容（单 Decider 模式）
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Dict, List

# 导入 providers 模块以触发 @decider 装饰器注册
from src.stages.decision import deciders  # noqa: F401
from src.stages.decision.manager import DeciderManager, SPEECH_DECIDERS
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.input import MessageReadyPayload
from src.modules.types.base.normalized_message import NormalizedMessage


# ==================== Fixtures ====================


@pytest.fixture
def mock_services():
    """提供 mock 的可选服务，供 Decider 类型匹配注入使用。

    LLMDecider 等会声明 LLMManager/PromptManager 等依赖，按类型匹配 DI
    在服务字典中找不到时直接抛错，因此测试需要预先注入 mock 实例。
    """
    return {
        "llm_service": MagicMock(),
        "prompt_service": MagicMock(),
        "config_service": MagicMock(),
        "context_service": MagicMock(),
    }


def _make_manager(mock_event_bus, mock_services=None):
    return DeciderManager(
        event_bus=mock_event_bus,
        llm_service=mock_services["llm_service"] if mock_services else None,
        prompt_manager=mock_services["prompt_service"] if mock_services else None,
        config_service=mock_services["config_service"] if mock_services else None,
        context_service=mock_services["context_service"] if mock_services else None,
    )


# ==================== 辅助函数 ====================


def create_mock_decider(name: str) -> MagicMock:
    """
    创建模拟 Decider

    Args:
        name: Decider 名称

    Returns:
        模拟的 Decider 对象
    """
    decider = MagicMock()
    decider.provider_name = name
    decider.decide = AsyncMock()
    decider.setup = AsyncMock()
    decider.cleanup = AsyncMock()
    return decider


def create_test_message(text: str = "测试消息") -> NormalizedMessage:
    """创建测试用 NormalizedMessage"""
    return NormalizedMessage(
        text=text,
        source="test_source",
        data_type="text",
        importance=0.5,
        timestamp_ms=1234567890000,
    )


# ==================== 测试类 ====================


class TestMultiDeciderConfigLoading:
    """测试多 Decider 配置加载"""

    @pytest.mark.asyncio
    async def test_load_single_decider_from_enabled(self, mock_services):
        """测试从 enabled 列表加载单个 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot"]}

        await manager.setup(decision_config=decision_config)

        # 验证只加载了一个 Decider
        assert len(manager._deciders) == 1
        assert "maibot" in manager._deciders
        assert manager.get_decider_names() == ["maibot"]

        # 向后兼容属性
        assert manager.get_current_decider_name() == "maibot"

    @pytest.mark.asyncio
    async def test_load_multiple_deciders_from_enabled_list(self, mock_services):
        """测试从 enabled 列表加载多个 Decider（新格式）"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot", "llm"]}

        await manager.setup(decision_config=decision_config)

        # 验证加载了多个 Decider
        assert len(manager._deciders) == 2
        assert "maibot" in manager._deciders
        assert "llm" in manager._deciders
        assert set(manager.get_decider_names()) == {"maibot", "llm"}

        # 向后兼容属性（指向第一个）
        assert manager.get_current_decider_name() in ["maibot", "llm"]

    @pytest.mark.asyncio
    async def test_load_multiple_deciders_with_decider_name_override(self, mock_services):
        """测试 decider_name 参数优先于配置"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot", "llm"]}

        # decider_name 优先于配置，只加载指定的 Decider
        await manager.setup(decider_name="llm", decision_config=decision_config)

        # 应该只加载指定的 Decider（llm，而不是配置中的 maibot 和 llm）
        assert len(manager._deciders) == 1
        assert "llm" in manager._deciders

    @pytest.mark.asyncio
    async def test_load_deciders_with_specific_config(self, mock_services):
        """测试每个 Decider 使用自己的配置"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {
            "enabled": ["maibot", "llm"],
            "maibot": {"host": "localhost", "port": 8000},
            "llm": {"model": "gpt-4"},
        }

        await manager.setup(decision_config=decision_config)

        # 验证两个 Decider 都加载了
        assert len(manager._deciders) == 2

    @pytest.mark.asyncio
    async def test_fallback_to_default_when_no_enabled_deciders(self, mock_services):
        """测试没有启用 Decider 时使用默认值"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        # 空配置
        decision_config = {}

        await manager.setup(decision_config=decision_config)

        # 应该使用默认的 maibot
        assert len(manager._deciders) == 1
        assert "maibot" in manager._deciders


class TestSpeechConflictWarning:
    """测试 Speech 冲突警告机制"""

    def test_speech_conflict_warning_for_multiple_speech_deciders(self, mock_services):
        """测试多个 speech-producing Decider 时的警告"""
        mock_event_bus = MagicMock()
        mock_logger = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)
        manager.logger = mock_logger

        decider_names = ["maibot", "llm"]  # 都是 speech deciders

        manager._check_speech_conflict(decider_names)

        # 验证警告日志
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "speech-producing Decider" in warning_msg
        assert "maibot" in warning_msg
        assert "llm" in warning_msg

    def test_no_warning_for_single_speech_decider(self, mock_services):
        """测试单个 speech decider 时无警告"""
        mock_event_bus = MagicMock()
        mock_logger = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)
        manager.logger = mock_logger

        decider_names = ["maibot"]  # 单个 speech decider

        manager._check_speech_conflict(decider_names)

        # 验证无警告
        mock_logger.warning.assert_not_called()

    def test_no_warning_for_non_speech_deciders(self, mock_services):
        """测试无 speech decider 时无警告"""
        mock_event_bus = MagicMock()
        mock_logger = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)
        manager.logger = mock_logger

        decider_names = ["command"]  # 非 speech decider

        manager._check_speech_conflict(decider_names)

        # 验证无警告
        mock_logger.warning.assert_not_called()

    def test_speech_deciders_constant_contains_expected_deciders(self, mock_services):
        """验证 SPEECH_DECIDERS 常量包含正确的 Decider"""
        assert "maibot" in SPEECH_DECIDERS
        assert "llm" in SPEECH_DECIDERS
        assert "command" not in SPEECH_DECIDERS  # command 产生 action，不产生 speech


class TestMultiDeciderLifecycle:
    """测试多 Decider 生命周期管理"""

    @pytest.mark.asyncio
    async def test_start_all_deciders(self, mock_services):
        """测试启动所有 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        # 只测试 maibot，因为它有简单的 setup 不需要 LLM
        decision_config = {"enabled": ["maibot"]}

        await manager.setup(decision_config=decision_config)
        await manager.start()

        # 验证 Decider 的 setup 被调用了
        assert manager._decider_ready.get("maibot") is True

    @pytest.mark.asyncio
    async def test_stop_all_deciders(self, mock_services):
        """测试停止所有 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot"]}

        await manager.setup(decision_config=decision_config)
        await manager.start()
        # 用 mock 替换真实 decider 避免 cleanup 时连 WebSocket
        for name in manager._deciders:
            manager._deciders[name] = create_mock_decider(name)
        await manager.stop()

        # 验证 Decider 已停止
        assert manager._decider_ready.get("maibot") is False

    @pytest.mark.asyncio
    async def test_cleanup_all_deciders(self, mock_services):
        """测试清理所有 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot", "llm"]}

        await manager.setup(decision_config=decision_config)
        await manager.cleanup()

        # 验证所有 Decider 已清理
        assert len(manager._deciders) == 0
        assert manager.get_current_decider() is None


class TestMultiDeciderDecision:
    """测试多 Decider 决策触发"""

    @pytest.mark.asyncio
    async def test_decide_triggers_all_deciders(self, mock_services):
        """测试 decide() 触发所有 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot", "llm"]}

        await manager.setup(decision_config=decision_config)

        # Mock 每个 decider 的 decide 方法
        for decider in manager._deciders.values():
            decider.decide = AsyncMock()

        test_message = create_test_message()
        await manager.decide(test_message)

        # 验证所有 Decider 的 decide 都被调用了
        for name, decider in manager._deciders.items():
            decider.decide.assert_called_once_with(test_message)

    @pytest.mark.asyncio
    async def test_decide_with_single_decider_backward_compat(self, mock_services):
        """测试单 Decider 模式（向后兼容）"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot"]}

        await manager.setup(decision_config=decision_config)

        # Mock decide
        manager._current_decider.decide = AsyncMock()

        test_message = create_test_message()
        await manager.decide(test_message)

        # 验证 decide 被调用
        manager._current_decider.decide.assert_called_once_with(test_message)


class TestBackwardCompatibility:
    """测试向后兼容性"""

    @pytest.mark.asyncio
    async def test_get_current_decider_returns_first_decider(self, mock_services):
        """测试 get_current_decider() 返回第一个 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot", "llm"]}

        await manager.setup(decision_config=decision_config)

        # get_current_decider 应该返回某个 Decider
        current = manager.get_current_decider()
        assert current is not None
        assert current.name in ["maibot", "llm"]

    @pytest.mark.asyncio
    async def test_switch_provider_with_existing_decider(self, mock_services):
        """测试切换到已加载的 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot", "llm"]}

        await manager.setup(decision_config=decision_config)

        # 切换到 llm
        await manager.switch_decider("llm", {})

        assert manager.get_current_decider_name() == "llm"

    @pytest.mark.asyncio
    async def test_get_deciders_returns_all_loaded_deciders(self, mock_services):
        """测试 get_deciders() 返回所有加载的 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot", "llm"]}

        await manager.setup(decision_config=decision_config)

        deciders = manager.get_deciders()

        assert len(deciders) == 2
        assert "maibot" in deciders
        assert "llm" in deciders

    @pytest.mark.asyncio
    async def test_get_decider_names_returns_name_list(self, mock_services):
        """测试 get_decider_names() 返回名称列表"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot", "llm"]}

        await manager.setup(decision_config=decision_config)

        names = manager.get_decider_names()

        assert len(names) == 2
        assert set(names) == {"maibot", "llm"}

    @pytest.mark.asyncio
    async def test_get_available_deciders(self, mock_services):
        """测试 get_available_deciders() 返回所有可用 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        available = manager.get_available_deciders()

        # 应该返回注册表中的所有 Decider
        assert isinstance(available, list)
        assert len(available) > 0


class TestEventSubscription:
    """测试事件订阅"""

    @pytest.mark.asyncio
    async def test_subscribe_input_message_ready_once(self, mock_services):
        """测试只订阅 INPUT_MESSAGE_RECEIVED 一次"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot", "llm"]}

        await manager.setup(decision_config=decision_config)
        await manager.start()

        # 验证 event_bus.on 只被调用一次
        mock_event_bus.on.assert_called_once_with(
            CoreEvents.INPUT_MESSAGE_RECEIVED,
            manager._on_data_message,
            model_class=MessageReadyPayload,
        )

    @pytest.mark.asyncio
    async def test_unsubscribe_on_stop(self, mock_services):
        """测试停止时取消订阅"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot"]}

        await manager.setup(decision_config=decision_config)
        await manager.start()
        # 用 mock 替换真实 decider 避免 cleanup 时连 WebSocket
        for name in manager._deciders:
            manager._deciders[name] = create_mock_decider(name)
        await manager.stop()

        # 验证 event_bus.off 被调用
        mock_event_bus.off.assert_called_once_with(
            CoreEvents.INPUT_MESSAGE_RECEIVED,
            manager._on_data_message,
        )


class TestEdgeCases:
    """边界情况测试"""

    @pytest.mark.asyncio
    async def test_decide_with_no_deciders(self, mock_services):
        """测试没有 Decider 时的 decide 调用"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        # 不调用 setup，直接调用 decide
        test_message = create_test_message()

        # 应该不抛出异常，只是记录警告
        await manager.decide(test_message)

    @pytest.mark.asyncio
    async def test_decider_failure_does_not_affect_others(self, mock_services):
        """测试单个 Decider 失败不影响其他 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": ["maibot", "llm"]}

        await manager.setup(decision_config=decision_config)

        # Mock 一个 Decider 失败
        manager._deciders["maibot"].decide = AsyncMock(side_effect=Exception("Decider failed"))
        manager._deciders["llm"].decide = AsyncMock()

        test_message = create_test_message()

        # 应该不抛出异常
        await manager.decide(test_message)

        # 成功的 Decider 仍然被调用了
        manager._deciders["llm"].decide.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_enabled_list_uses_default(self, mock_services):
        """测试空 enabled 列表使用默认 Decider"""
        mock_event_bus = MagicMock()
        manager = _make_manager(mock_event_bus, mock_services)

        decision_config = {"enabled": []}

        await manager.setup(decision_config=decision_config)

        # 应该使用默认的 maibot
        assert len(manager._deciders) == 1
        assert "maibot" in manager._deciders
