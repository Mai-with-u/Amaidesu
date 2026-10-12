"""Emotion 全局词表测试

覆盖：
1. 词表成员数：17 值（基础 7 / 次级 5 / 主播向 5）。
2. 全部值小写（跨 LLM/配置/事件/存储边界一律小写的契约）。
3. 枚举成员名与值的对应关系（含三组的完整性）。

运行: uv run pytest tests/modules/types/test_emotion_vocab.py -v
"""

from src.modules.types.emotion_vocab import Emotion


class TestEmotionVocabMembership:
    """词表成员构成"""

    def test_seventeen_values(self):
        """词表 17 值"""
        assert len(Emotion) == 17

    def test_base_seven(self):
        """基础 7：Ekman 6 + neutral"""
        expected = {"neutral", "happy", "sad", "angry", "surprised", "scared", "disgusted"}
        assert expected <= {e.value for e in Emotion}

    def test_secondary_five(self):
        """次级 5：自我意识与唤起家族"""
        expected = {"shy", "embarrassed", "confused", "love", "excited"}
        assert expected <= {e.value for e in Emotion}

    def test_streamer_five(self):
        """主播向 5：直播行业高频"""
        expected = {"smug", "serious", "tired", "crying", "speechless"}
        assert expected <= {e.value for e in Emotion}

    def test_removed_values_absent(self):
        """thinking / relaxed 已剔除，不再属于词表"""
        values = {e.value for e in Emotion}
        assert "thinking" not in values
        assert "relaxed" not in values


class TestEmotionVocabCase:
    """大小写契约"""

    def test_all_values_lowercase(self):
        """全部值小写：值一律小写（跨边界），枚举成员名大写仅 Python 内部"""
        for member in Emotion:
            assert member.value == member.value.lower(), f"{member.name} 的值 {member.value!r} 非小写"

    def test_member_names_uppercase(self):
        """枚举成员名大写（仅 Python 内部约定）"""
        for member in Emotion:
            assert member.name == member.name.upper()

    def test_str_enum_serializes_to_value(self):
        """str 枚举：序列化时直接是字符串值"""
        assert Emotion.HAPPY == "happy"
        assert str(Emotion.HAPPY.value) == "happy"
        assert {e.value for e in Emotion} == {e for e in Emotion}


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-s"])
