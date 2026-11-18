"""reply 工具的情绪值收敛：中英文别名与不可识别值的兜底语义。"""

from __future__ import annotations

from src.agents.streamer.tools.reply_tool import coerce_emotion, known_emotion_label
from src.modules.types.emotion_vocab import Emotion


def test_cn_emotion_name_maps_to_enum_with_note() -> None:
    emotion, note = coerce_emotion("害羞")
    assert emotion is Emotion.SHY and note


def test_english_alias_still_maps() -> None:
    emotion, note = coerce_emotion("Cheerful")
    assert emotion is Emotion.HAPPY and note


def test_enum_value_passes_without_note() -> None:
    assert coerce_emotion("shy") == (Emotion.SHY, "")


def test_unknown_value_falls_back_to_neutral() -> None:
    emotion, _ = coerce_emotion("莫名其妙的话")
    assert emotion is Emotion.NEUTRAL


def test_known_emotion_label_covers_cn_en_and_enum_only() -> None:
    assert known_emotion_label("害羞") is True
    assert known_emotion_label("playful") is True
    assert known_emotion_label("neutral") is True
    assert known_emotion_label("大家") is False
    assert known_emotion_label("") is False
