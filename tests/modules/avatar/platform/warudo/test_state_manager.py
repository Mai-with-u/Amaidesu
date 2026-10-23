"""Warudo 状态管理器：表情/眼/瞳孔/元音/视线各状态件的写入规则与下发，以及变化驱动的监控循环"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.avatar.platform.warudo.state.warudo_state_manager import (
    ALL_PUPIL_STATE,
    ALL_SIGHT_STATE,
    ExpressionState,
    EyeState,
    PupilState,
    SightState,
    VowelState,
    WarudoStateManager,
)


async def _sent(state) -> dict:
    callback = AsyncMock()
    await state.send_state(callback)
    return {call.args[0]: call.args[1] for call in callback.call_args_list}


async def test_sight_and_pupil_keep_their_fixed_protocol() -> None:
    sight = SightState()
    assert sight.changed is True and sight.get_sight_state() == ALL_SIGHT_STATE  # 新建即待发送，默认全零
    sight.set_state("camera", 0.5)
    sight.set_state("danmu", 0.7)
    assert sight.first_layer == {"camera": 0.0, "danmu": 0.7, "phone": 0.0}  # 视线互斥，后设覆盖先设
    camera = SightState()
    camera.set_state("camera", 1.0)
    assert await _sent(camera) == {"sight": "漫游"}  # 视线只发非零项，看镜头按漫游目标下发

    pupil = PupilState()
    pupil.set_state("eye_shift_left", 0.5)
    pupil.set_state("eye_shift_up", 0.7)
    sent = await _sent(pupil)
    assert set(sent) == set(ALL_PUPIL_STATE) and (sent["eye_shift_left"], sent["eye_shift_up"]) == (0.5, 0.7)


async def test_expression_replaces_the_whole_table_and_skips_the_blink_key() -> None:
    state = ExpressionState(["happy", "sad", "eye_close"])
    assert set(state.weights) == {"happy", "sad"}  # 眨眼键归眼部状态管，不进表情表

    state.set_weights({"happy": 0.8, "eye_close": 0.4})
    state.set_weights({"sad": 1.5})
    assert await _sent(state) == {"happy": 0.0, "sad": 1.0}  # 换情绪整表重发：旧键清零、权重夹到 0~1


async def test_mouth_keys_yield_to_lipsync_and_come_back_after_speaking() -> None:
    state = ExpressionState(["mouth_happy_strong", "eyebrow_happy_weak"], mouth_keys=["mouth_happy_strong"])
    state.set_weights({"mouth_happy_strong": 1.0, "eyebrow_happy_weak": 0.8})
    state.changed = False

    state.set_speaking(True)
    assert state.changed is True and await _sent(state) == {"mouth_happy_strong": 0.0, "eyebrow_happy_weak": 0.8}
    state.set_speaking(False)
    assert await _sent(state) == {"mouth_happy_strong": 1.0, "eyebrow_happy_weak": 0.8}  # 停嘴后嘴形恢复

    plain = ExpressionState(["happy"])
    plain.changed = False
    plain.set_speaking(True)
    assert plain.changed is False  # 档案没有嘴部表情键时说话不触发重发


def test_eye_blink_yields_to_expression_eyes() -> None:
    eye = EyeState()
    assert eye.can_blink() is True and eye.get_eye_state() == {"eye_close": 0.0}

    eye.set_expression(0.6, blink_blocked=False)
    assert eye.can_blink() is False and eye.get_eye_state() == {"eye_close": 0.6}  # 情绪半闭眼时不眨眼
    eye.set_expression(0.0, blink_blocked=True)
    assert eye.can_blink() is False  # 笑眼等暂停眨眼的键生效
    eye.set_expression(0.0, blink_blocked=False)
    eye.set_blinking(True)
    assert eye.get_eye_state() == {"eye_close": 1.0}  # 眨眼中闭满


def test_vowel_keeps_one_active_vowel_and_clears_on_empty_key() -> None:
    vowel = VowelState()
    vowel.set_vowel("VowelA", 1.5)
    vowel.set_vowel("VowelO", 0.6)
    assert (vowel.weights["VowelA"], vowel.weights["VowelO"], vowel.active) == (0.0, 0.6, True)
    vowel.set_vowel("", 0.0)
    assert vowel.active is False and set(vowel.weights.values()) == {0.0}


@pytest.fixture
def state_manager() -> WarudoStateManager:
    return WarudoStateManager(MagicMock(), lambda action, data: None, expression_keys=["happy"])


async def test_monitoring_start_and_stop_are_idempotent(state_manager: WarudoStateManager) -> None:
    assert state_manager._is_monitoring is False
    state_manager.start_monitoring()
    first_task = state_manager._monitoring_task
    state_manager.start_monitoring()
    assert state_manager._is_monitoring is True and state_manager._monitoring_task is first_task  # 不重复建任务

    state_manager.stop_monitoring()
    state_manager.stop_monitoring()
    assert state_manager._is_monitoring is False


@pytest.mark.parametrize("changed", [True, False])
async def test_monitor_loop_sends_only_changed_states(state_manager: WarudoStateManager, changed: bool) -> None:
    for state in (
        state_manager.vowel_state,
        state_manager.expression_state,
        state_manager.eye_state,
        state_manager.pupil_state,
        state_manager.sight_state,
    ):
        state.changed = changed
    callback = AsyncMock()
    state_manager.send_action_callback = callback

    state_manager.start_monitoring()
    await asyncio.sleep(0.2)
    state_manager.stop_monitoring()

    assert callback.called is changed  # 全部未变化时一条都不发
