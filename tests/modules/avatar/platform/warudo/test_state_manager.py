"""Warudo 状态管理器：分层表情状态（视线/眉/眼/瞳孔/嘴）的写入规则与发送，以及变化驱动的监控循环"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.avatar.platform.warudo.state.warudo_state_manager import (
    ALL_EYE_STATE,
    ALL_EYEBROW_STATE,
    ALL_MOUTH_STATE,
    ALL_PUPIL_STATE,
    ALL_SIGHT_STATE,
    EyebrowState,
    EyeState,
    MouthState,
    PupilState,
    SightState,
    WarudoStateManager,
)


@pytest.mark.parametrize(
    ("state_cls", "getter", "defaults", "key"),
    [
        (SightState, "get_sight_state", ALL_SIGHT_STATE, "camera"),
        (EyebrowState, "get_eyebrow_state", ALL_EYEBROW_STATE, "eyebrow_happy_strong"),
        (EyeState, "get_eye_state", ALL_EYE_STATE, "eye_happy_strong"),
        (PupilState, "get_pupil_state", ALL_PUPIL_STATE, "eye_shift_down"),
        (MouthState, "get_mouth_state", ALL_MOUTH_STATE, "mouth_happy_strong"),
    ],
    ids=["sight", "eyebrow", "eye", "pupil", "mouth"],
)
async def test_layered_state_defaults_setter_and_send(state_cls, getter, defaults, key) -> None:
    state = state_cls()
    assert state.changed is True and getattr(state, getter)() == defaults  # 新建即标记待发送，默认全零

    setter = state.set_state if hasattr(state, "set_state") else state.set_first_layer
    setter(key, 0.7)
    assert getattr(state, getter)()[key] == 0.7 and state.changed is True

    callback = AsyncMock()
    await state.send_state(callback)
    if state_cls is SightState:
        callback.assert_called_once_with("sight", "漫游")  # 视线只发非零项，且按漫游目标下发
    else:
        assert callback.call_count == len(defaults)  # 其余状态整表下发（含零值），保证旧值被清掉


def test_exclusive_layers_clear_previous_while_pupil_keeps_others() -> None:
    sight = SightState()
    sight.set_state("camera", 0.5)
    sight.set_state("danmu", 0.7)
    assert sight.first_layer == {"camera": 0.0, "danmu": 0.7, "phone": 0.0}  # 视线互斥，后设覆盖先设

    eyebrow = EyebrowState()
    eyebrow.set_first_layer("eyebrow_happy_weak", 0.5)
    eyebrow.set_first_layer("eyebrow_angry_strong", 0.9)
    assert (eyebrow.first_layer["eyebrow_happy_weak"], eyebrow.first_layer["eyebrow_angry_strong"]) == (0.0, 0.9)

    pupil = PupilState()
    pupil.set_state("eye_shift_left", 0.5)
    pupil.set_state("eye_shift_up", 0.7)
    assert (pupil.first_layer["eye_shift_left"], pupil.first_layer["eye_shift_up"]) == (0.5, 0.7)  # 瞳孔可叠加


def test_eye_blink_rules() -> None:
    eye = EyeState()
    assert eye.first_layer == {"eye_happy_strong": 0.0, "eye_close": 0.0} and eye.can_blink() is True
    for key in ("eye_happy_strong", "eye_close"):  # 笑眼或已闭眼时不再眨眼
        closed = EyeState()
        closed.set_first_layer(key, 1.0)
        assert closed.can_blink() is False

    eye.set_blinking(True)
    assert eye.is_blinking is True and eye.get_eye_state()["eye_close"] == 1.0


def test_mouth_vowel_layer_overrides_expression_layer_and_clamps() -> None:
    mouth = MouthState()
    mouth.set_first_layer("mouth_happy_strong", 0.6)
    mouth.set_vowel_state({"VowelA": 1.5, "VowelI": -0.5, "VowelU": 0.7})
    assert (mouth.second_layer["VowelA"], mouth.second_layer["VowelI"], mouth.second_layer["VowelU"]) == (1.0, 0.0, 0.7)

    mouth.set_vowel_state({"VowelA": 0.8})
    result = mouth.get_mouth_state()
    assert result["VowelA"] == 0.8 and result["mouth_happy_strong"] == 0.0  # 口型层覆盖表情层，表情值不出现


@pytest.fixture
def state_manager() -> WarudoStateManager:
    return WarudoStateManager(MagicMock(), lambda action, data: None)


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
        state_manager.mouth_state,
        state_manager.eye_state,
        state_manager.pupil_state,
        state_manager.eyebrow_state,
        state_manager.sight_state,
    ):
        state.changed = changed
    callback = AsyncMock()
    state_manager.send_action_callback = callback

    state_manager.start_monitoring()
    await asyncio.sleep(0.2)
    state_manager.stop_monitoring()

    assert callback.called is changed  # 全部未变化时一条都不发
