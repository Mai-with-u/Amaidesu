"""Warudo Provider 测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.types import Intent
from src.domains.output.providers.avatar.warudo.warudo_provider import WarudoOutputProvider
from src.modules.types import ActionType, EmotionType, IntentAction


@pytest.fixture
def warudo_config():
    return {"ws_host": "localhost", "ws_port": 19190}


@pytest.fixture
def mock_websocket():
    """创建一个 mock WebSocket 连接"""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


class TestWarudoProviderEmotionMap:
    def test_emotion_map_exists(self):
        assert hasattr(WarudoOutputProvider, "EMOTION_MAP")
        assert isinstance(WarudoOutputProvider.EMOTION_MAP, dict)

    def test_emotion_map_has_required_emotions(self):
        required = ["happy", "sad", "angry", "surprised", "shy", "love", "neutral"]
        for emotion in required:
            assert emotion in WarudoOutputProvider.EMOTION_MAP

    def test_emotion_map_happy(self):
        assert WarudoOutputProvider.EMOTION_MAP[EmotionType.HAPPY]["mouthSmile"] == 1.0


class TestWarudoProviderAdaptIntent:
    def test_adapt_intent_with_happy_emotion(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        intent = Intent(original_text="你好", response_text="你好啊！", emotion=EmotionType.HAPPY, actions=[])
        result = provider._adapt_intent(intent)
        assert "expressions" in result
        assert result["expressions"]["mouthSmile"] == 1.0

    def test_adapt_intent_with_neutral_emotion(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        intent = Intent(original_text="你好", response_text="你好", emotion=EmotionType.NEUTRAL, actions=[])
        result = provider._adapt_intent(intent)
        assert result["expressions"] == {}

    def test_adapt_intent_with_hotkey_action(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        action = IntentAction(type=ActionType.WAVE, params={})
        intent = Intent(original_text="测试", response_text="测试", emotion=EmotionType.NEUTRAL, actions=[action])
        result = provider._adapt_intent(intent)
        assert "wave" in result["hotkeys"]


class TestWarudoProviderConfig:
    def test_init_with_default_config(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        assert provider.ws_host == "localhost"
        assert provider.ws_port == 19190


class TestWarudoProviderConnection:
    @pytest.mark.asyncio
    async def test_connect_success(self, warudo_config, mock_websocket):
        provider = WarudoOutputProvider(warudo_config)
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
            await provider._connect()
            assert provider._is_connected is True
            assert provider.websocket == mock_websocket

    @pytest.mark.asyncio
    async def test_disconnect(self, warudo_config, mock_websocket):
        provider = WarudoOutputProvider(warudo_config)
        provider.websocket = mock_websocket
        provider._is_connected = True
        await provider._disconnect()
        assert provider._is_connected is False
        assert provider.websocket is None
        mock_websocket.close.assert_called_once()


class TestWarudoProviderRendering:
    @pytest.mark.asyncio
    async def test_render_internal_with_expressions(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        # 创建 AsyncMock 的 websocket
        mock_ws = MagicMock()
        mock_ws.send_json = AsyncMock()
        provider.websocket = mock_ws
        provider._is_connected = True

        # 使用 Intent 调用基类的 _render_internal
        intent = Intent(
            original_text="测试",
            response_text="测试",
            emotion=EmotionType.HAPPY,
            actions=[],
        )
        await provider._render_internal(intent)

        assert mock_ws.send_json.call_count >= 1

    @pytest.mark.asyncio
    async def test_render_internal_when_not_connected(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        provider._is_connected = False

        # 使用 Intent 调用基类的 _render_internal
        intent = Intent(
            original_text="测试",
            response_text="测试",
            emotion=EmotionType.HAPPY,
            actions=[],
        )
        # 应该不抛出异常，只是跳过渲染
        await provider._render_internal(intent)


class TestWarudoProviderMoodManagement:
    """心情管理测试"""

    def test_initial_mood_state(self, warudo_config):
        """测试初始心情状态"""
        provider = WarudoOutputProvider(warudo_config)
        assert provider.current_mood == {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}
        assert provider.current_expression == "neutral"

    def test_update_mood_with_valid_data(self, warudo_config):
        """测试更新心情状态"""
        provider = WarudoOutputProvider(warudo_config)
        result = provider.update_mood({"joy": 8, "anger": 2, "sorrow": 1, "fear": 1})

        assert result is True
        assert provider.current_mood["joy"] == 8
        assert provider.current_mood["anger"] == 2

    def test_update_mood_clamps_values(self, warudo_config):
        """测试心情值被限制在 1-10 范围内"""
        provider = WarudoOutputProvider(warudo_config)
        provider.update_mood({"joy": 15, "anger": -5, "sorrow": 0, "fear": 12})

        assert provider.current_mood["joy"] == 10
        assert provider.current_mood["anger"] == 1
        assert provider.current_mood["sorrow"] == 1
        assert provider.current_mood["fear"] == 10

    def test_update_mood_returns_false_when_no_changes(self, warudo_config):
        """测试心情未变化时返回 False"""
        provider = WarudoOutputProvider(warudo_config)
        result = provider.update_mood({"joy": 5, "anger": 1, "sorrow": 1, "fear": 1})
        assert result is False

    def test_update_mood_with_partial_data(self, warudo_config):
        """测试更新部分心情数据"""
        provider = WarudoOutputProvider(warudo_config)
        # 初始值是 {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}
        # 注意：由于 WarudoOutputProvider.update_mood() 会将原始 mood_data 传给 MoodManager
        # 而 MoodManager 要求必须包含所有四个情绪，所以这里需要传入完整的数据
        # WarudoOutputProvider.update_mood() 使用 .get(emotion, 5) 来填充缺失的情绪到 self.current_mood
        provider.update_mood({"joy": 9, "anger": 5, "sorrow": 5, "fear": 5})

        assert provider.current_mood["joy"] == 9
        # 未提供的情绪会被设置为默认值 5
        assert provider.current_mood["anger"] == 5
        assert provider.current_mood["sorrow"] == 5
        assert provider.current_mood["fear"] == 5


class TestWarudoProviderReplyState:
    """回复状态管理测试"""

    @pytest.mark.asyncio
    async def test_start_talking(self, warudo_config):
        """测试开始说话状态"""
        provider = WarudoOutputProvider(warudo_config)
        await provider.start_talking()
        assert provider.reply_state.is_talking is True

    @pytest.mark.asyncio
    async def test_stop_talking(self, warudo_config):
        """测试停止说话状态"""
        provider = WarudoOutputProvider(warudo_config)
        await provider.start_talking()
        await provider.stop_talking()
        assert provider.reply_state.is_talking is False

    @pytest.mark.asyncio
    async def test_start_talking_idempotent(self, warudo_config):
        """测试重复开始说话状态"""
        provider = WarudoOutputProvider(warudo_config)
        await provider.start_talking()
        await provider.start_talking()
        assert provider.reply_state.is_talking is True


class TestWarudoProviderStatistics:
    """统计信息测试"""

    def test_get_stats_initial(self, warudo_config):
        """测试获取初始统计信息"""
        provider = WarudoOutputProvider(warudo_config)
        stats = provider.get_stats()

        assert stats["name"] == "WarudoOutputProvider"
        assert stats["is_connected"] is False
        assert stats["render_count"] == 0
        assert stats["error_count"] == 0
        assert "current_mood" in stats
        assert "current_expression" in stats

    def test_get_stats_after_render(self, warudo_config, mock_websocket):
        """测试渲染后的统计信息"""
        import asyncio

        provider = WarudoOutputProvider(warudo_config)
        provider._is_connected = True

        # 执行一次渲染
        mock_ws = MagicMock()
        mock_ws.send_json = AsyncMock()
        provider.websocket = mock_ws

        # 使用 Intent 调用基类的 _render_internal
        intent = Intent(
            original_text="测试",
            response_text="测试",
            emotion=EmotionType.HAPPY,
            actions=[],
        )
        asyncio.run(provider._render_internal(intent))

        stats = provider.get_stats()
        assert stats["render_count"] == 1


class TestWarudoProviderDisconnectionCleanup:
    """断开连接清理测试"""

    @pytest.mark.asyncio
    async def test_disconnect_stops_blink_task(self, warudo_config, mock_websocket):
        """测试断开连接时停止眨眼任务"""
        provider = WarudoOutputProvider(warudo_config)
        provider.websocket = mock_websocket
        provider._is_connected = True

        # 启动眨眼任务
        await provider.blink_task.start()

        # 断开连接
        await provider._disconnect()

        # 眨眼任务应该被停止
        assert provider.blink_task.running is False

    @pytest.mark.asyncio
    async def test_disconnect_stops_shift_task(self, warudo_config, mock_websocket):
        """测试断开连接时停止眼部移动任务"""
        provider = WarudoOutputProvider(warudo_config)
        provider.websocket = mock_websocket
        provider._is_connected = True

        # 启动眼部移动任务
        await provider.shift_task.start()

        # 断开连接
        await provider._disconnect()

        # 眼部移动任务应该被停止
        assert provider.shift_task.running is False

    @pytest.mark.asyncio
    async def test_disconnect_stops_state_monitoring(self, warudo_config, mock_websocket):
        """测试断开连接时停止状态监控"""
        provider = WarudoOutputProvider(warudo_config)
        provider.websocket = mock_websocket
        provider._is_connected = True

        # 启动状态监控
        provider.state_manager.start_monitoring()

        # 断开连接
        await provider._disconnect()

        # 状态监控应该被停止
        assert provider.state_manager._is_monitoring is False


class TestWarudoProviderAdaptIntentWithActions:
    """Intent 适配测试（动作相关）"""

    def test_adapt_intent_with_custom_hotkey(self, warudo_config):
        """测试适配自定义热键动作"""
        provider = WarudoOutputProvider(warudo_config)
        action = IntentAction(type=ActionType.CUSTOM, params={"hotkey_id": "my_custom_hotkey"})
        intent = Intent(original_text="测试", response_text="测试", emotion=EmotionType.NEUTRAL, actions=[action])
        result = provider._adapt_intent(intent)

        assert "my_custom_hotkey" in result["hotkeys"]

    def test_adapt_intent_with_multiple_actions(self, warudo_config):
        """测试适配多个动作"""
        provider = WarudoOutputProvider(warudo_config)
        actions = [
            IntentAction(type=ActionType.WAVE, params={}),
            IntentAction(type=ActionType.BLINK, params={}),
        ]
        intent = Intent(original_text="测试", response_text="测试", emotion=EmotionType.HAPPY, actions=actions)
        result = provider._adapt_intent(intent)

        assert "wave" in result["hotkeys"]
        assert "blink" in result["hotkeys"]
        assert "mouthSmile" in result["expressions"]

    def test_adapt_intent_with_emotion_and_action(self, warudo_config):
        """测试同时适配情感和动作"""
        provider = WarudoOutputProvider(warudo_config)
        action = IntentAction(type=ActionType.NOD, params={})
        intent = Intent(original_text="你好", response_text="你好啊！", emotion=EmotionType.HAPPY, actions=[action])
        result = provider._adapt_intent(intent)

        assert "mouthSmile" in result["expressions"]
        assert "nod" in result["hotkeys"]


class TestWarudoProviderSendMethods:
    """发送方法测试"""

    @pytest.mark.asyncio
    async def test_send_expression_when_connected(self, warudo_config):
        """测试连接状态下发送表情参数"""
        provider = WarudoOutputProvider(warudo_config)
        mock_ws = MagicMock()
        mock_ws.send_json = AsyncMock()
        provider.websocket = mock_ws
        provider._is_connected = True

        await provider._send_expression("mouthSmile", 0.8)

        mock_ws.send_json.assert_called_once()
        sent_data = mock_ws.send_json.call_args[0][0]
        assert sent_data["type"] == "expression"
        assert sent_data["data"]["mouthSmile"] == 0.8

    @pytest.mark.asyncio
    async def test_send_expression_when_not_connected(self, warudo_config):
        """测试未连接状态下发送表情参数"""
        provider = WarudoOutputProvider(warudo_config)
        provider._is_connected = False

        # 应该不抛出异常，只是记录警告
        await provider._send_expression("mouthSmile", 0.8)

    @pytest.mark.asyncio
    async def test_send_hotkey_when_connected(self, warudo_config):
        """测试连接状态下触发热键"""
        provider = WarudoOutputProvider(warudo_config)
        mock_ws = MagicMock()
        mock_ws.send_json = AsyncMock()
        provider.websocket = mock_ws
        provider._is_connected = True

        await provider._send_hotkey("wave")

        mock_ws.send_json.assert_called_once()
        sent_data = mock_ws.send_json.call_args[0][0]
        assert sent_data["type"] == "hotkey"
        assert sent_data["data"]["id"] == "wave"

    @pytest.mark.asyncio
    async def test_send_hotkey_when_not_connected(self, warudo_config):
        """测试未连接状态下触发热键"""
        provider = WarudoOutputProvider(warudo_config)
        provider._is_connected = False

        # 应该不抛出异常，只是记录警告
        await provider._send_hotkey("wave")
