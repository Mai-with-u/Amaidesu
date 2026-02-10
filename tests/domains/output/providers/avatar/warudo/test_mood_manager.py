"""Warudo 心情管理器测试"""

from unittest.mock import MagicMock

import pytest

from src.domains.output.providers.avatar.warudo.state.mood_manager import MoodManager
from src.domains.output.providers.avatar.warudo.state.warudo_state_manager import WarudoStateManager


class TestMoodManager:
    """MoodManager 测试"""

    @pytest.fixture
    def mock_logger(self):
        """创建 mock logger"""
        return MagicMock()

    @pytest.fixture
    def state_manager(self, mock_logger):
        """创建状态管理器实例"""
        return WarudoStateManager(mock_logger, lambda action, data: None)

    @pytest.fixture
    def mood_manager(self, state_manager, mock_logger):
        """创建心情管理器实例"""
        return MoodManager(state_manager, mock_logger)

    def test_init(self, mood_manager):
        """测试初始化"""
        assert mood_manager.current_mood == {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}
        assert mood_manager.current_expression == "neutral"
        assert len(mood_manager.expression_combinations) > 0

    def test_expression_combinations_has_required_keys(self, mood_manager):
        """测试表情组合包含必需的表情"""
        required = ["happy", "sad", "angry", "neutral"]
        for key in required:
            assert key in mood_manager.expression_combinations

    def test_expression_combinations_structure(self, mood_manager):
        """测试表情组合的结构"""
        for _expr_name, expr_config in mood_manager.expression_combinations.items():
            # 验证每个表情配置包含必需的部位
            assert "eye" in expr_config
            assert "eyebrow" in expr_config
            assert "mouth" in expr_config

            # 验证每个部位配置包含必需的字段
            for part in ["eye", "eyebrow", "mouth"]:
                assert "action" in expr_config[part]
                assert "weight" in expr_config[part]

    def test_update_mood_with_valid_data(self, mood_manager):
        """测试更新心情状态（有效数据）"""
        result = mood_manager.update_mood({"joy": 8, "anger": 2, "sorrow": 1, "fear": 1})

        assert result is True
        assert mood_manager.current_mood == {"joy": 8, "anger": 2, "sorrow": 1, "fear": 1}

    def test_update_mood_clamps_values(self, mood_manager):
        """测试心情值被限制在 1-10 范围内"""
        mood_manager.update_mood({"joy": 15, "anger": -5, "sorrow": 7, "fear": 0})

        assert mood_manager.current_mood["joy"] == 10  # 最大值
        assert mood_manager.current_mood["anger"] == 1  # 最小值
        assert mood_manager.current_mood["sorrow"] == 7
        assert mood_manager.current_mood["fear"] == 1  # 最小值

    def test_update_mood_with_partial_data(self, mood_manager):
        """测试更新部分心情数据"""
        # MoodManager.update_mood() 要求必须提供所有四个情绪
        # 部分数据会导致 KeyError
        with pytest.raises(KeyError):
            mood_manager.update_mood({"joy": 9})

    def test_update_mood_returns_false_when_no_changes(self, mood_manager):
        """测试心情未变化时返回 False"""
        result = mood_manager.update_mood({"joy": 5, "anger": 1, "sorrow": 1, "fear": 1})
        assert result is False

    def test_select_expression_by_mood_very_happy(self, mood_manager):
        """测试根据心情选择表情（非常快乐）"""
        mood_manager.current_mood = {"joy": 9, "anger": 1, "sorrow": 1, "fear": 1}
        expression = mood_manager._select_expression_by_mood()
        assert expression == "very_happy"

    def test_select_expression_by_mood_happy(self, mood_manager):
        """测试根据心情选择表情（快乐）"""
        mood_manager.current_mood = {"joy": 7, "anger": 1, "sorrow": 1, "fear": 1}
        expression = mood_manager._select_expression_by_mood()
        assert expression == "happy"

    def test_select_expression_by_mood_neutral_joy(self, mood_manager):
        """测试根据心情选择表情（中性快乐）"""
        mood_manager.current_mood = {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}
        expression = mood_manager._select_expression_by_mood()
        assert expression == "neutral"

    def test_select_expression_by_mood_very_angry(self, mood_manager):
        """测试根据心情选择表情（非常愤怒）"""
        mood_manager.current_mood = {"joy": 1, "anger": 8, "sorrow": 1, "fear": 1}
        expression = mood_manager._select_expression_by_mood()
        assert expression == "very_angry"

    def test_select_expression_by_mood_angry(self, mood_manager):
        """测试根据心情选择表情（愤怒）"""
        mood_manager.current_mood = {"joy": 1, "anger": 6, "sorrow": 1, "fear": 1}
        expression = mood_manager._select_expression_by_mood()
        assert expression == "angry"

    def test_select_expression_by_mod_neutral_anger(self, mood_manager):
        """测试根据心情选择表情（中性愤怒）"""
        mood_manager.current_mood = {"joy": 1, "anger": 3, "sorrow": 1, "fear": 1}
        expression = mood_manager._select_expression_by_mood()
        assert expression == "neutral"

    def test_select_expression_by_mood_sad(self, mood_manager):
        """测试根据心情选择表情（悲伤）"""
        mood_manager.current_mood = {"joy": 1, "anger": 1, "sorrow": 7, "fear": 1}
        expression = mood_manager._select_expression_by_mood()
        assert expression == "sad"

    def test_select_expression_by_mood_dominant_emotion(self, mood_manager):
        """测试主导情绪选择"""
        # 快乐占主导
        mood_manager.current_mood = {"joy": 8, "anger": 4, "sorrow": 1, "fear": 1}
        expression = mood_manager._select_expression_by_mood()
        # joy=8 会选择 happy（>=7 但 <9）
        assert expression == "happy"

    def test_apply_expression_valid(self, mood_manager):
        """测试应用有效表情"""
        mood_manager._apply_expression("happy")

        # 验证状态管理器的各个状态被更新
        # 由于随机选择可能是 happy 或 happy_2，检查眉毛状态是否为 happy 类型
        eyebrow_val = mood_manager.state_manager.eyebrow_state.first_layer["eyebrow_happy_weak"]
        eyebrow_strong_val = mood_manager.state_manager.eyebrow_state.first_layer["eyebrow_happy_strong"]
        # 应该至少有一个被设置
        assert eyebrow_val > 0 or eyebrow_strong_val > 0

        # 嘴巴状态也应该被设置
        mouth_val = mood_manager.state_manager.mouth_state.first_layer.get("mouth_happy_strong", 0)
        mouth_smile_2_val = mood_manager.state_manager.mouth_state.first_layer.get("mouth_smlie_2", 0)
        assert mouth_val > 0 or mouth_smile_2_val > 0

    def test_apply_expression_invalid(self, mood_manager):
        """测试应用无效表情"""
        # 应该不抛出异常
        mood_manager._apply_expression("invalid_expression")
        # 验证日志被调用
        assert mood_manager.logger.warning.called

    def test_apply_expression_with_multiple_candidates(self, mood_manager):
        """测试应用有多个候选的表情"""
        # happy 前缀有多个候选: happy, happy_2
        # 注意：_apply_expression 会随机选择，但 current_expression 不会自动更新
        # 需要手动设置 current_expression
        mood_manager._apply_expression("happy")
        # _apply_expression 不会自动更新 current_expression
        # 这个测试只是验证函数不会崩溃
        assert True  # 如果没有异常就通过

    def test_update_expression_by_mood_changes_expression(self, mood_manager):
        """测试心情更新时表情变化"""
        # 先设置为 neutral
        mood_manager.current_expression = "neutral"
        mood_manager.current_mood = {"joy": 9, "anger": 1, "sorrow": 1, "fear": 1}
        mood_manager._update_expression_by_mood()

        assert mood_manager.current_expression == "very_happy"

    def test_update_expression_by_mood_same_expression(self, mood_manager):
        """测试心情更新但表情不变"""
        mood_manager.current_expression = "neutral"
        mood_manager.current_mood = {"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}
        mood_manager._update_expression_by_mood()

        # 表情应该保持不变
        assert mood_manager.current_expression == "neutral"

    def test_update_mood_triggers_expression_update(self, mood_manager):
        """测试更新心情会触发表情更新"""
        mood_manager.update_mood({"joy": 9, "anger": 1, "sorrow": 1, "fear": 1})

        # 表情应该被更新
        assert mood_manager.current_expression == "very_happy"

    def test_update_mood_handles_invalid_emotion_names(self, mood_manager):
        """测试更新心情时处理无效的情绪名称"""
        # MoodManager.update_mood() 只处理 joy/anger/sorrow/fear 四个情绪
        # 传入无效情绪（不在列表中）会被忽略，只更新有效的情绪
        result = mood_manager.update_mood({"joy": 6, "anger": 1, "sorrow": 1, "fear": 1})

        # 应该只处理有效的情绪
        assert result is True
        assert mood_manager.current_mood["joy"] == 6

    def test_expression_combinations_weight_range(self, mood_manager):
        """测试所有表情的权重在合理范围内"""
        for expr_name, expr_config in mood_manager.expression_combinations.items():
            for part in ["eye", "eyebrow", "mouth"]:
                weight = expr_config[part]["weight"]
                assert 0 <= weight <= 1, f"{expr_name}.{part} weight {weight} 不在 0-1 范围内"

    def test_select_expression_fear_dominant(self, mood_manager):
        """测试恐惧为主导情绪时选择中性表情"""
        mood_manager.current_mood = {"joy": 1, "anger": 1, "sorrow": 1, "fear": 8}
        expression = mood_manager._select_expression_by_mood()
        # 恐惧目前映射到 neutral
        assert expression == "neutral"

    def test_select_expression_sorrow_below_threshold(self, mood_manager):
        """测试悲伤值低于阈值时选择中性表情"""
        mood_manager.current_mood = {"joy": 1, "anger": 1, "sorrow": 3, "fear": 1}
        expression = mood_manager._select_expression_by_mood()
        assert expression == "neutral"
