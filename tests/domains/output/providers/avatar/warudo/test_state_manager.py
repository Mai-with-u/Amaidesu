"""Warudo 状态管理器测试"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.domains.output.providers.avatar.warudo.state.warudo_state_manager import (
    SightState,
    EyebrowState,
    EyeState,
    PupilState,
    MouthState,
    WarudoStateManager,
    ALL_SIGHT_STATE,
    ALL_EYEBROW_STATE,
    ALL_EYE_STATE,
    ALL_PUPIL_STATE,
    ALL_MOUTH_STATE,
)


class TestSightState:
    """SightState 测试"""

    def test_init(self):
        """测试初始化"""
        state = SightState()
        assert state.first_layer == {"camera": 0.0, "danmu": 0.0, "phone": 0.0}
        assert state.changed is True

    def test_set_state(self):
        """测试设置视线状态"""
        state = SightState()
        state.set_state("camera", 0.8)

        assert state.first_layer == {"camera": 0.8, "danmu": 0.0, "phone": 0.0}
        assert state.changed is True

    def test_set_state_clears_previous(self):
        """测试设置状态时会清除之前的状态"""
        state = SightState()
        state.set_state("camera", 0.5)
        state.set_state("danmu", 0.7)

        # camera 应该被清除
        assert state.first_layer["camera"] == 0.0
        assert state.first_layer["danmu"] == 0.7

    def test_get_sight_state_default(self):
        """测试获取默认视线状态"""
        state = SightState()
        result = state.get_sight_state()
        assert result == ALL_SIGHT_STATE

    def test_get_sight_state_with_values(self):
        """测试获取有值的视线状态"""
        state = SightState()
        state.set_state("camera", 0.8)
        result = state.get_sight_state()

        assert result["camera"] == 0.8
        assert result["danmu"] == 0.0
        assert result["phone"] == 0.0

    @pytest.mark.asyncio
    async def test_send_state(self):
        """测试发送视线状态"""
        state = SightState()
        state.set_state("camera", 0.8)

        mock_callback = AsyncMock()
        await state.send_state(mock_callback)

        # 验证只发送非零状态
        mock_callback.assert_called_once_with("sight", "漫游")


class TestEyebrowState:
    """EyebrowState 测试"""

    def test_init(self):
        """测试初始化"""
        state = EyebrowState()
        assert state.changed is True
        assert all(v == 0.0 for v in state.first_layer.values())

    def test_set_first_layer(self):
        """测试设置第一层状态"""
        state = EyebrowState()
        state.set_first_layer("eyebrow_happy_weak", 0.8)

        assert state.first_layer["eyebrow_happy_weak"] == 0.8
        assert state.changed is True

    def test_set_first_layer_clears_previous(self):
        """测试设置时会清除之前的状态"""
        state = EyebrowState()
        state.set_first_layer("eyebrow_happy_weak", 0.5)
        state.set_first_layer("eyebrow_angry_strong", 0.9)

        assert state.first_layer["eyebrow_happy_weak"] == 0.0
        assert state.first_layer["eyebrow_angry_strong"] == 0.9

    def test_get_eyebrow_state_default(self):
        """测试获取默认眉毛状态"""
        state = EyebrowState()
        result = state.get_eyebrow_state()
        assert result == ALL_EYEBROW_STATE

    def test_get_eyebrow_state_with_values(self):
        """测试获取有值的眉毛状态"""
        state = EyebrowState()
        state.set_first_layer("eyebrow_happy_strong", 0.7)
        result = state.get_eyebrow_state()

        assert result["eyebrow_happy_strong"] == 0.7

    @pytest.mark.asyncio
    async def test_send_state(self):
        """测试发送眉毛状态"""
        state = EyebrowState()
        state.set_first_layer("eyebrow_happy_weak", 0.6)

        mock_callback = AsyncMock()
        await state.send_state(mock_callback)

        # 应该发送所有状态（包括零值）
        assert mock_callback.call_count == len(ALL_EYEBROW_STATE)


class TestEyeState:
    """EyeState 测试"""

    def test_init(self):
        """测试初始化"""
        state = EyeState()
        assert state.first_layer == {"eye_happy_strong": 0.0, "eye_close": 0.0}
        assert state.is_blinking is False
        assert state.changed is True

    def test_can_blink_default(self):
        """测试默认可以眨眼"""
        state = EyeState()
        assert state.can_blink() is True

    def test_can_blink_with_eye_happy_strong(self):
        """测试有 eye_happy_strong 时不能眨眼"""
        state = EyeState()
        state.set_first_layer("eye_happy_strong", 1.0)
        assert state.can_blink() is False

    def test_can_blink_with_eye_close(self):
        """测试有 eye_close 时不能眨眼"""
        state = EyeState()
        state.set_first_layer("eye_close", 1.0)
        assert state.can_blink() is False

    def test_set_blinking(self):
        """测试设置眨眼状态"""
        state = EyeState()
        state.set_blinking(True)
        assert state.is_blinking is True
        assert state.changed is True

    def test_get_eye_state_blinking(self):
        """测试获取眨眼状态"""
        state = EyeState()
        state.set_blinking(True)
        result = state.get_eye_state()

        assert result["eye_close"] == 1.0

    def test_get_eye_state_normal(self):
        """测试获取正常眼睛状态"""
        state = EyeState()
        result = state.get_eye_state()

        assert result == ALL_EYE_STATE

    @pytest.mark.asyncio
    async def test_send_state(self):
        """测试发送眼睛状态"""
        state = EyeState()
        state.set_first_layer("eye_happy_strong", 0.9)

        mock_callback = AsyncMock()
        await state.send_state(mock_callback)

        assert mock_callback.call_count == len(ALL_EYE_STATE)


class TestPupilState:
    """PupilState 测试"""

    def test_init(self):
        """测试初始化"""
        state = PupilState()
        assert state.changed is True
        assert all(v == 0.0 for v in state.first_layer.values())

    def test_set_state(self):
        """测试设置瞳孔状态"""
        state = PupilState()
        state.set_state("eye_shift_left", 0.8)

        assert state.first_layer["eye_shift_left"] == 0.8
        assert state.changed is True

    def test_set_state_preserves_others(self):
        """测试设置状态时保留其他状态"""
        state = PupilState()
        state.set_state("eye_shift_left", 0.5)
        state.set_state("eye_shift_up", 0.7)

        assert state.first_layer["eye_shift_left"] == 0.5
        assert state.first_layer["eye_shift_up"] == 0.7
        assert state.first_layer["eye_shift_right"] == 0.0

    def test_get_pupil_state_default(self):
        """测试获取默认瞳孔状态"""
        state = PupilState()
        result = state.get_pupil_state()
        assert result == ALL_PUPIL_STATE

    def test_get_pupil_state_with_values(self):
        """测试获取有值的瞳孔状态"""
        state = PupilState()
        state.set_state("eye_shift_down", 0.6)
        result = state.get_pupil_state()

        assert result["eye_shift_down"] == 0.6

    @pytest.mark.asyncio
    async def test_send_state(self):
        """测试发送瞳孔状态"""
        state = PupilState()
        state.set_state("eye_shift_right", 0.5)

        mock_callback = AsyncMock()
        await state.send_state(mock_callback)

        assert mock_callback.call_count == len(ALL_PUPIL_STATE)


class TestMouthState:
    """MouthState 测试"""

    def test_init(self):
        """测试初始化"""
        state = MouthState()
        assert state.changed is True
        assert all(v == 0.0 for v in state.first_layer.values())
        assert all(v == 0.0 for v in state.second_layer.values())

    def test_set_first_layer(self):
        """测试设置第一层状态"""
        state = MouthState()
        state.set_first_layer("mouth_happy_strong", 0.7)

        assert state.first_layer["mouth_happy_strong"] == 0.7
        assert state.changed is True

    def test_set_vowel_state(self):
        """测试设置口型状态"""
        state = MouthState()
        state.set_vowel_state({"VowelA": 0.8, "VowelI": 0.5})

        assert state.second_layer["VowelA"] == 0.8
        assert state.second_layer["VowelI"] == 0.5
        assert state.changed is True

    def test_set_vowel_state_clamps_values(self):
        """测试口型状态值被限制在 0-1 范围内"""
        state = MouthState()
        state.set_vowel_state({"VowelA": 1.5, "VowelI": -0.5, "VowelU": 0.7})

        assert state.second_layer["VowelA"] == 1.0
        assert state.second_layer["VowelI"] == 0.0
        assert state.second_layer["VowelU"] == 0.7

    def test_get_mouth_state_default(self):
        """测试获取默认嘴巴状态"""
        state = MouthState()
        result = state.get_mouth_state()
        assert result == ALL_MOUTH_STATE

    def test_get_mouth_state_with_first_layer(self):
        """测试获取第一层嘴巴状态"""
        state = MouthState()
        state.set_first_layer("mouth_happy_strong", 0.6)
        result = state.get_mouth_state()

        assert result["mouth_happy_strong"] == 0.6

    def test_get_mouth_state_prioritizes_second_layer(self):
        """测试口型状态优先于表情状态"""
        state = MouthState()
        state.set_first_layer("mouth_happy_strong", 0.6)
        state.set_vowel_state({"VowelA": 0.8})
        result = state.get_mouth_state()

        # 第二层（口型）应该覆盖第一层
        assert result["VowelA"] == 0.8
        # 第一层的值不应该出现在结果中
        assert result["mouth_happy_strong"] == 0.0

    @pytest.mark.asyncio
    async def test_send_state(self):
        """测试发送嘴巴状态"""
        state = MouthState()
        state.set_first_layer("mouth_happy_strong", 0.8)

        mock_callback = AsyncMock()
        await state.send_state(mock_callback)

        assert mock_callback.call_count == len(ALL_MOUTH_STATE)


class TestWarudoStateManager:
    """WarudoStateManager 测试"""

    @pytest.fixture
    def mock_logger(self):
        """创建 mock logger"""
        return MagicMock()

    @pytest.fixture
    def state_manager(self, mock_logger):
        """创建状态管理器实例"""
        return WarudoStateManager(mock_logger, lambda action, data: None)

    def test_init(self, state_manager):
        """测试初始化"""
        assert state_manager.eye_state is not None
        assert state_manager.pupil_state is not None
        assert state_manager.mouth_state is not None
        assert state_manager.eyebrow_state is not None
        assert state_manager.sight_state is not None
        assert state_manager._is_monitoring is False

    @pytest.mark.asyncio
    async def test_start_monitoring(self, state_manager):
        """测试启动监控"""
        state_manager.start_monitoring()
        assert state_manager._is_monitoring is True
        assert state_manager._monitoring_task is not None

        # 清理
        state_manager.stop_monitoring()

    @pytest.mark.asyncio
    async def test_start_monitoring_idempotent(self, state_manager):
        """测试重复启动监控不会创建多个任务"""
        state_manager.start_monitoring()
        first_task = state_manager._monitoring_task

        state_manager.start_monitoring()
        second_task = state_manager._monitoring_task

        assert first_task == second_task

        # 清理
        state_manager.stop_monitoring()

    @pytest.mark.asyncio
    async def test_stop_monitoring(self, state_manager):
        """测试停止监控"""
        state_manager.start_monitoring()
        state_manager.stop_monitoring()

        assert state_manager._is_monitoring is False

    @pytest.mark.asyncio
    async def test_stop_monitoring_idempotent(self, state_manager):
        """测试重复停止监控不会出错"""
        state_manager.start_monitoring()
        state_manager.stop_monitoring()

        # 再次停止应该不会出错
        state_manager.stop_monitoring()
        assert state_manager._is_monitoring is False

    @pytest.mark.asyncio
    async def test_monitor_loop(self, state_manager):
        """测试监控循环"""
        state_manager.start_monitoring()

        # 等待一小段时间让监控循环运行
        await asyncio.sleep(0.2)

        # 停止监控
        state_manager.stop_monitoring()

        # 验证日志被调用（监控循环会记录日志）
        assert state_manager.logger.info.called or state_manager.logger.debug.called

    @pytest.mark.asyncio
    async def test_monitor_loop_sends_changed_states(self, state_manager):
        """测试监控循环发送变化的状态"""
        # 标记一些状态为已变化
        state_manager.mouth_state.changed = True
        state_manager.eye_state.changed = True

        # 创建 mock callback
        mock_callback = AsyncMock()
        state_manager.send_action_callback = mock_callback

        state_manager.start_monitoring()
        await asyncio.sleep(0.2)
        state_manager.stop_monitoring()

        # 验证 send_action 被调用
        assert mock_callback.called

    @pytest.mark.asyncio
    async def test_monitor_loop_only_sends_changed_states(self, state_manager):
        """测试监控循环只发送变化的状态"""
        # 所有状态都标记为未变化
        state_manager.mouth_state.changed = False
        state_manager.eye_state.changed = False
        state_manager.pupil_state.changed = False
        state_manager.eyebrow_state.changed = False
        state_manager.sight_state.changed = False

        # 创建 mock callback
        mock_callback = AsyncMock()
        state_manager.send_action_callback = mock_callback

        state_manager.start_monitoring()
        await asyncio.sleep(0.2)
        state_manager.stop_monitoring()

        # send_action 不应该被调用（因为所有状态都未变化）
        assert not mock_callback.called
