"""
ActionMapper 测试

测试 ActionMapper 的动作映射功能
"""

from src.domains.output.parameters.action_mapper import ActionMapper
from src.domains.decision.intent import ActionType, IntentAction


class TestActionMapperInit:
    """测试 ActionMapper 初始化"""

    def test_init(self):
        """测试初始化"""
        mapper = ActionMapper()
        assert mapper is not None


class TestActionMapperMapActions:
    """测试动作映射功能"""

    def test_map_empty_actions(self):
        """测试映射空动作列表"""
        mapper = ActionMapper()
        result = mapper.map_actions([])

        assert result["hotkeys"] == []
        assert result["actions"] == []
        assert result["expressions"] == {}

    def test_map_single_emoji_action(self):
        """测试映射单个表情动作"""
        mapper = ActionMapper()
        actions = [IntentAction(type=ActionType.EMOJI, params={"emoji": "😀"}, priority=50)]
        result = mapper.map_actions(actions)

        assert len(result["actions"]) == 1
        assert result["actions"][0]["type"] == "emoji"
        assert result["actions"][0]["params"] == {"emoji": "😀"}

    def test_map_single_hotkey_action(self):
        """测试映射单个热键动作"""
        mapper = ActionMapper()
        actions = [IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "test_hotkey_1"}, priority=50)]
        result = mapper.map_actions(actions)

        assert len(result["hotkeys"]) == 1
        assert result["hotkeys"][0] == "test_hotkey_1"

    def test_map_single_expression_action(self):
        """测试映射单个表情参数动作"""
        mapper = ActionMapper()
        actions = [
            IntentAction(
                type=ActionType.EXPRESSION, params={"expressions": {"MouthSmile": 0.8, "EyeOpenLeft": 0.9}}, priority=50
            )
        ]
        result = mapper.map_actions(actions)

        assert result["expressions"] == {"MouthSmile": 0.8, "EyeOpenLeft": 0.9}

    def test_map_multiple_hotkey_actions(self):
        """测试映射多个热键动作"""
        mapper = ActionMapper()
        actions = [
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "hotkey_1"}, priority=50),
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "hotkey_2"}, priority=60),
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "hotkey_3"}, priority=40),
        ]
        result = mapper.map_actions(actions)

        assert len(result["hotkeys"]) == 3
        # 应该按优先级排序（priority 40, 50, 60）
        assert result["hotkeys"][0] == "hotkey_3"
        assert result["hotkeys"][1] == "hotkey_1"
        assert result["hotkeys"][2] == "hotkey_2"

    def test_map_mixed_action_types(self):
        """测试映射混合类型的动作"""
        mapper = ActionMapper()
        actions = [
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "hotkey_1"}, priority=50),
            IntentAction(type=ActionType.EMOJI, params={"emoji": "😀"}, priority=60),
            IntentAction(type=ActionType.EXPRESSION, params={"expressions": {"MouthSmile": 0.5}}, priority=70),
        ]
        result = mapper.map_actions(actions)

        assert len(result["hotkeys"]) == 1
        assert len(result["actions"]) == 1
        assert result["expressions"] == {"MouthSmile": 0.5}


class TestActionMapperHandlers:
    """测试各种动作处理器"""

    def test_handle_emoji_action(self):
        """测试表情动作处理器"""
        mapper = ActionMapper()
        action = IntentAction(type=ActionType.EMOJI, params={"emoji": "😀", "size": "large"}, priority=50)
        result = {"hotkeys": [], "actions": [], "expressions": {}}

        mapper.handle_emoji_action(action, result)

        assert len(result["actions"]) == 1
        assert result["actions"][0]["type"] == "emoji"
        assert result["actions"][0]["params"] == {"emoji": "😀", "size": "large"}

    def test_handle_hotkey_action_with_id(self):
        """测试热键动作处理器（有 ID）"""
        mapper = ActionMapper()
        action = IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "test_hotkey"}, priority=50)
        result = {"hotkeys": [], "actions": [], "expressions": {}}

        mapper.handle_hotkey_action(action, result)

        assert len(result["hotkeys"]) == 1
        assert result["hotkeys"][0] == "test_hotkey"

    def test_handle_hotkey_action_without_id(self):
        """测试热键动作处理器（无 ID）"""
        mapper = ActionMapper()
        action = IntentAction(
            type=ActionType.HOTKEY,
            params={},  # 没有 hotkey_id
            priority=50,
        )
        result = {"hotkeys": [], "actions": [], "expressions": {}}

        mapper.handle_hotkey_action(action, result)

        assert len(result["hotkeys"]) == 0

    def test_handle_expression_action(self):
        """测试表情参数动作处理器"""
        mapper = ActionMapper()
        action = IntentAction(
            type=ActionType.EXPRESSION, params={"expressions": {"MouthSmile": 0.8, "EyeOpenLeft": 0.9}}, priority=50
        )
        result = {"hotkeys": [], "actions": [], "expressions": {}}

        mapper.handle_expression_action(action, result)

        assert result["expressions"] == {"MouthSmile": 0.8, "EyeOpenLeft": 0.9}

    def test_handle_expression_action_merge(self):
        """测试表情参数合并"""
        mapper = ActionMapper()
        action1 = IntentAction(type=ActionType.EXPRESSION, params={"expressions": {"MouthSmile": 0.8}}, priority=50)
        action2 = IntentAction(type=ActionType.EXPRESSION, params={"expressions": {"EyeOpenLeft": 0.9}}, priority=60)
        result = {"hotkeys": [], "actions": [], "expressions": {}}

        mapper.handle_expression_action(action1, result)
        mapper.handle_expression_action(action2, result)

        # 两个表情参数应该合并
        assert result["expressions"] == {"MouthSmile": 0.8, "EyeOpenLeft": 0.9}

    def test_handle_text_action(self):
        """测试文本动作处理器"""
        mapper = ActionMapper()
        action = IntentAction(
            type=ActionType.EXPRESSION,  # 使用存在的类型
            params={"text": "test"},
            priority=50,
        )
        result = {"hotkeys": [], "actions": [], "expressions": {}}

        mapper.handle_text_action(action, result)

        # 文本动作不添加任何内容
        assert len(result["actions"]) == 0

    def test_handle_motion_action(self):
        """测试动作动作处理器"""
        mapper = ActionMapper()
        action = IntentAction(
            type=ActionType.EXPRESSION,  # 使用存在的类型
            params={"motion": "wave"},
            priority=50,
        )
        result = {"hotkeys": [], "actions": [], "expressions": {}}

        mapper.handle_motion_action(action, result)

        assert len(result["actions"]) == 1
        assert result["actions"][0]["type"] == "motion"
        assert result["actions"][0]["params"] == {"motion": "wave"}

    def test_handle_custom_action(self):
        """测试自定义动作处理器"""
        mapper = ActionMapper()
        action = IntentAction(
            type=ActionType.EXPRESSION,  # 使用存在的类型
            params={"custom": "value"},
            priority=50,
        )
        result = {"hotkeys": [], "actions": [], "expressions": {}}

        mapper.handle_custom_action(action, result)

        assert len(result["actions"]) == 1
        assert result["actions"][0]["type"] == "custom"
        assert result["actions"][0]["params"] == {"custom": "value"}


class TestActionMapperPriority:
    """测试优先级排序"""

    def test_priority_sorting_ascending(self):
        """测试优先级升序排序（数字越小越优先）"""
        mapper = ActionMapper()
        actions = [
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "priority_50"}, priority=50),
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "priority_10"}, priority=10),
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "priority_90"}, priority=90),
        ]
        result = mapper.map_actions(actions)

        # 应该按优先级升序排列
        assert result["hotkeys"][0] == "priority_10"
        assert result["hotkeys"][1] == "priority_50"
        assert result["hotkeys"][2] == "priority_90"

    def test_priority_same_order(self):
        """测试相同优先级的动作"""
        mapper = ActionMapper()
        actions = [
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "first"}, priority=50),
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "second"}, priority=50),
        ]
        result = mapper.map_actions(actions)

        # 相同优先级时，保持原始顺序（稳定排序）
        assert len(result["hotkeys"]) == 2
        assert result["hotkeys"][0] == "first"
        assert result["hotkeys"][1] == "second"

    def test_priority_extreme_values(self):
        """测试极端优先级值"""
        mapper = ActionMapper()
        actions = [
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "min"}, priority=0),
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "max"}, priority=100),
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "mid"}, priority=50),
        ]
        result = mapper.map_actions(actions)

        assert result["hotkeys"][0] == "min"
        assert result["hotkeys"][1] == "mid"
        assert result["hotkeys"][2] == "max"


class TestActionMapperEdgeCases:
    """测试边界情况"""

    def test_hotkey_action_with_missing_hotkey_id(self):
        """测试热键动作缺少 hotkey_id"""
        mapper = ActionMapper()
        actions = [IntentAction(type=ActionType.HOTKEY, params={"other_param": "value"}, priority=50)]
        result = mapper.map_actions(actions)

        assert len(result["hotkeys"]) == 0

    def test_expression_action_with_empty_expressions(self):
        """测试表情动作的 expressions 参数为空"""
        mapper = ActionMapper()
        actions = [IntentAction(type=ActionType.EXPRESSION, params={"expressions": {}}, priority=50)]
        result = mapper.map_actions(actions)

        assert result["expressions"] == {}

    def test_expression_action_with_missing_expressions_key(self):
        """测试表情动作缺少 expressions 参数"""
        mapper = ActionMapper()
        actions = [IntentAction(type=ActionType.EXPRESSION, params={"other_key": "value"}, priority=50)]
        result = mapper.map_actions(actions)

        # 应该使用默认的空字典
        assert result["expressions"] == {}

    def test_action_with_empty_params(self):
        """测试动作的 params 为空字典"""
        mapper = ActionMapper()
        actions = [IntentAction(type=ActionType.EMOJI, params={}, priority=50)]
        result = mapper.map_actions(actions)

        # 仍应创建动作，只是 params 为空
        assert len(result["actions"]) == 1
        assert result["actions"][0]["params"] == {}

    def test_unknown_action_type(self):
        """测试未知动作类型"""
        # 这个测试验证 ActionMapper 的行为
        # 对于未在 DEFAULT_ACTION_HANDLERS 中定义的类型，不会调用任何处理器
        mapper = ActionMapper()

        # 创建一个动作，但不是所有类型都有处理器
        # 根据代码，只有 DEFAULT_ACTION_HANDLERS 中定义的类型会被处理
        actions = [
            IntentAction(
                type=ActionType.BLINK,  # 有处理器
                params={"duration": 0.5},
                priority=50,
            ),
        ]
        result = mapper.map_actions(actions)

        # BLINK 有处理器，但处理器不存在，所以不会添加任何内容
        assert len(result["actions"]) == 0

    def test_actions_with_negative_priority(self):
        """测试负数优先级"""
        mapper = ActionMapper()
        actions = [
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "negative"}, priority=-10),
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "positive"}, priority=10),
        ]
        result = mapper.map_actions(actions)

        assert result["hotkeys"][0] == "negative"
        assert result["hotkeys"][1] == "positive"

    def test_large_number_of_actions(self):
        """测试大量动作"""
        mapper = ActionMapper()
        actions = []
        for i in range(100):
            actions.append(IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": f"hotkey_{i}"}, priority=i))
        result = mapper.map_actions(actions)

        assert len(result["hotkeys"]) == 100
        assert result["hotkeys"][0] == "hotkey_0"
        assert result["hotkeys"][99] == "hotkey_99"


class TestActionMapperResultStructure:
    """测试返回结果的结构"""

    def test_result_has_all_keys(self):
        """测试结果包含所有必需的键"""
        mapper = ActionMapper()
        result = mapper.map_actions([])

        assert "hotkeys" in result
        assert "actions" in result
        assert "expressions" in result

    def test_result_values_are_correct_types(self):
        """测试结果值的类型正确"""
        mapper = ActionMapper()
        result = mapper.map_actions([])

        assert isinstance(result["hotkeys"], list)
        assert isinstance(result["actions"], list)
        assert isinstance(result["expressions"], dict)

    def test_result_is_new_each_call(self):
        """测试每次调用返回新的结果"""
        mapper = ActionMapper()
        result1 = mapper.map_actions([])
        result2 = mapper.map_actions([])

        # 结果应该是不同的对象
        assert result1 is not result2
        assert result1["hotkeys"] is not result2["hotkeys"]
        assert result1["actions"] is not result2["actions"]
        assert result1["expressions"] is not result2["expressions"]


class TestActionMapperMultipleExpressions:
    """测试多个表情动作的合并"""

    def test_multiple_expressions_merge(self):
        """测试多个表情动作的参数合并"""
        mapper = ActionMapper()
        actions = [
            IntentAction(
                type=ActionType.EXPRESSION, params={"expressions": {"MouthSmile": 0.5, "EyeOpenLeft": 0.5}}, priority=50
            ),
            IntentAction(
                type=ActionType.EXPRESSION,
                params={"expressions": {"MouthSmile": 0.8, "EyeOpenRight": 0.9}},
                priority=60,
            ),
        ]
        result = mapper.map_actions(actions)

        # 后面的表情参数应该覆盖前面的
        assert result["expressions"]["MouthSmile"] == 0.8
        assert result["expressions"]["EyeOpenLeft"] == 0.5
        assert result["expressions"]["EyeOpenRight"] == 0.9

    def test_expression_and_other_actions(self):
        """测试表情动作与其他动作的组合"""
        mapper = ActionMapper()
        actions = [
            IntentAction(type=ActionType.EXPRESSION, params={"expressions": {"MouthSmile": 0.5}}, priority=50),
            IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "test_hotkey"}, priority=60),
            IntentAction(type=ActionType.EMOJI, params={"emoji": "😀"}, priority=70),
        ]
        result = mapper.map_actions(actions)

        assert len(result["expressions"]) == 1
        assert len(result["hotkeys"]) == 1
        assert len(result["actions"]) == 1
