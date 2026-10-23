"""Warudo 表情档案：默认 VRM 标准档案覆盖全部情绪、麦麦档案可直接加载、错名错键在加载期拦下"""

import pytest

from src.modules.avatar.platform.warudo.expression_profile import (
    MAIMAI_PROFILE_PATH,
    WarudoExpressionConfig,
    read_profile_expression,
)
from src.modules.types.emotion_vocab import Emotion


def test_default_profile_covers_every_emotion_with_vrm_standard_names() -> None:
    profile = WarudoExpressionConfig()
    assert set(profile.emotions) == {e.value for e in Emotion}
    # 默认档案只用 VRM 1.0 标准情绪槽 + 眨眼键，任何 VRM 模型都能接
    assert profile.expression_keys() == ["angry", "happy", "relaxed", "sad", "surprised"]


def test_maimai_profile_loads_and_keys_follow_the_model_shapes() -> None:
    profile = WarudoExpressionConfig.from_dict(read_profile_expression(MAIMAI_PROFILE_PATH))
    assert set(profile.emotions) == {e.value for e in Emotion}
    keys = set(profile.expression_keys())
    # 用模型里真实存在的形状名：笑眼 eye_happy_strong、露齿笑 mouth_simle_teeth（模型自身拼写）
    assert {"eye_happy_strong", "mouth_simle_teeth"} <= keys and "mouth_smlie_teeth" not in keys
    assert set(profile.mouth_keys) <= keys and "eye_close" not in keys  # 眨眼键归眼部状态管


@pytest.mark.parametrize(
    ("emotions", "message"),
    [
        ({"joyful": {"happy": 1.0}}, "joyful"),  # 词表外情绪
        ({"happy": {"VowelA": 1.0}}, "VowelA"),  # 口型专用键
        ({"happy": {"eye_shift_left": 1.0}}, "eye_shift_left"),  # 移眼专用键
        ({"happy": {"happy": 1.5}}, "1.5"),  # 权重越界
    ],
)
def test_bad_profile_entries_fail_at_load_time(emotions: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        WarudoExpressionConfig.from_dict({"emotions": emotions})
