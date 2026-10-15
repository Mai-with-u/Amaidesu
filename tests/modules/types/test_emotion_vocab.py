"""Emotion 全局词表：17 值（基础 7 / 次级 5 / 主播向 5），值一律小写（跨 LLM/配置/事件/存储边界的契约）"""

from src.modules.types.emotion_vocab import Emotion

BASE = {"neutral", "happy", "sad", "angry", "surprised", "scared", "disgusted"}  # Ekman 6 + neutral
SECONDARY = {"shy", "embarrassed", "confused", "love", "excited"}
STREAMER = {"smug", "serious", "tired", "crying", "speechless"}


def test_vocab_is_exactly_the_three_groups_and_retired_values_are_absent() -> None:
    values = {e.value for e in Emotion}
    assert len(Emotion) == 17 and values == BASE | SECONDARY | STREAMER
    assert not values & {"thinking", "relaxed"}  # 已剔除


def test_values_are_lowercase_names_uppercase_and_str_enum_compares_to_its_value() -> None:
    assert all(e.value == e.value.lower() and e.name == e.name.upper() for e in Emotion)
    assert Emotion.HAPPY == "happy"
