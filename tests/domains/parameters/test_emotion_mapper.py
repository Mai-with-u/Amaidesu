"""
EmotionMapper 测试

测试 EmotionMapper 的情感映射功能
"""

from src.domains.output.parameters.emotion_mapper import EmotionMapper
from src.domains.decision.intent import EmotionType


class TestEmotionMapperInit:
    """测试 EmotionMapper 初始化"""

    def test_init_with_default_map(self):
        """测试使用默认映射初始化"""
        mapper = EmotionMapper()
        assert mapper.emotion_map is not None
        assert len(mapper.emotion_map) > 0
        assert EmotionType.NEUTRAL in mapper.emotion_map
        assert EmotionType.HAPPY in mapper.emotion_map

    def test_init_with_custom_map(self):
        """测试使用自定义映射初始化"""
        custom_map = {
            EmotionType.HAPPY: {"MouthSmile": 1.0, "EyeOpenLeft": 0.9},
            EmotionType.SAD: {"MouthSmile": 0.0, "EyeOpenLeft": 0.5},
        }
        mapper = EmotionMapper(custom_map)

        assert mapper.emotion_map == custom_map
        assert len(mapper.emotion_map) == 2

    def test_init_with_none_map(self):
        """测试传入 None 时使用默认映射"""
        mapper = EmotionMapper(None)
        assert mapper.emotion_map == EmotionMapper.DEFAULT_EMOTION_MAP


class TestEmotionMapperMapEmotion:
    """测试情感映射功能"""

    def test_map_neutral_emotion(self):
        """测试映射 NEUTRAL 情感"""
        mapper = EmotionMapper()
        params = mapper.map_emotion(EmotionType.NEUTRAL)

        assert isinstance(params, dict)
        assert "MouthSmile" in params
        assert "EyeOpenLeft" in params
        assert "EyeOpenRight" in params
        assert params["MouthSmile"] == 0.0
        assert params["EyeOpenLeft"] == 1.0
        assert params["EyeOpenRight"] == 1.0

    def test_map_happy_emotion(self):
        """测试映射 HAPPY 情感"""
        mapper = EmotionMapper()
        params = mapper.map_emotion(EmotionType.HAPPY)

        assert isinstance(params, dict)
        assert params["MouthSmile"] == 0.8
        assert params["EyeOpenLeft"] == 0.85
        assert params["EyeOpenRight"] == 0.85
        assert params["BrowLeftY"] == 0.2
        assert params["BrowRightY"] == 0.2

    def test_map_sad_emotion(self):
        """测试映射 SAD 情感"""
        mapper = EmotionMapper()
        params = mapper.map_emotion(EmotionType.SAD)

        assert isinstance(params, dict)
        assert params["MouthSmile"] == -0.3
        assert params["EyeOpenLeft"] == 0.7
        assert params["EyeOpenRight"] == 0.7
        assert params["BrowLeftY"] == -0.4
        assert params["BrowRightY"] == -0.4

    def test_map_angry_emotion(self):
        """测试映射 ANGRY 情感"""
        mapper = EmotionMapper()
        params = mapper.map_emotion(EmotionType.ANGRY)

        assert isinstance(params, dict)
        assert params["MouthSmile"] == -0.2
        assert params["EyeOpenLeft"] == 0.9
        assert params["EyeOpenRight"] == 0.9
        assert params["BrowLeftY"] == -0.6
        assert params["BrowRightY"] == -0.6

    def test_map_surprised_emotion(self):
        """测试映射 SURPRISED 情感"""
        mapper = EmotionMapper()
        params = mapper.map_emotion(EmotionType.SURPRISED)

        assert isinstance(params, dict)
        assert params["MouthSmile"] == 0.1
        assert params["MouthOpen"] == 0.5
        assert params["EyeOpenLeft"] == 1.2
        assert params["EyeOpenRight"] == 1.2
        assert params["BrowLeftY"] == 0.5
        assert params["BrowRightY"] == 0.5

    def test_map_love_emotion(self):
        """测试映射 LOVE 情感"""
        mapper = EmotionMapper()
        params = mapper.map_emotion(EmotionType.LOVE)

        assert isinstance(params, dict)
        assert params["MouthSmile"] == 0.7
        assert params["EyeOpenLeft"] == 0.8
        assert params["EyeOpenRight"] == 0.8
        assert params["CheekPuff"] == 0.3

    def test_map_emotion_returns_copy(self):
        """测试映射返回的是副本，不是原始字典的引用"""
        mapper = EmotionMapper()
        params1 = mapper.map_emotion(EmotionType.HAPPY)
        params2 = mapper.map_emotion(EmotionType.HAPPY)

        # 修改返回的字典不应影响原始映射
        params1["MouthSmile"] = 1.0
        assert params2["MouthSmile"] == 0.8

        # 再次映射应该得到原始值
        params3 = mapper.map_emotion(EmotionType.HAPPY)
        assert params3["MouthSmile"] == 0.8


class TestEmotionMapperSetEmotionMapping:
    """测试设置情感映射功能"""

    def test_set_emotion_mapping_for_happy(self):
        """测试设置 HAPPY 情感映射"""
        mapper = EmotionMapper()
        new_params = {
            "MouthSmile": 1.0,
            "EyeOpenLeft": 0.5,
            "EyeOpenRight": 0.5,
        }
        mapper.set_emotion_mapping(EmotionType.HAPPY, new_params)

        mapped_params = mapper.map_emotion(EmotionType.HAPPY)
        assert mapped_params == new_params

    def test_set_emotion_mapping_adds_new_emotion(self):
        """测试添加新的情感映射"""
        mapper = EmotionMapper()
        # 移除一个情感
        if EmotionType.SAD in mapper.emotion_map:
            del mapper.emotion_map[EmotionType.SAD]

        # 重新添加
        new_params = {"MouthSmile": 0.0, "EyeOpenLeft": 0.3}
        mapper.set_emotion_mapping(EmotionType.SAD, new_params)

        assert EmotionType.SAD in mapper.emotion_map
        assert mapper.map_emotion(EmotionType.SAD) == new_params

    def test_set_emotion_mapping_saves_copy(self):
        """测试设置映射时保存的是副本"""
        mapper = EmotionMapper()
        original_params = {"MouthSmile": 0.5, "EyeOpenLeft": 0.5}
        mapper.set_emotion_mapping(EmotionType.HAPPY, original_params)

        # 修改原始字典不应影响已保存的映射
        original_params["MouthSmile"] = 1.0
        mapped_params = mapper.map_emotion(EmotionType.HAPPY)

        assert mapped_params["MouthSmile"] == 0.5


class TestEmotionMapperAddEmotion:
    """测试添加情感功能"""

    def test_add_emotion_with_valid_name(self):
        """测试添加有效情感名称"""
        mapper = EmotionMapper()
        initial_count = len(mapper.get_available_emotions())

        # 添加一个已存在的情感类型
        mapper.add_emotion("happy", {"MouthSmile": 1.0})

        # 情感数量应该不变（因为已存在）
        assert len(mapper.get_available_emotions()) == initial_count

    def test_add_emotion_with_invalid_name(self):
        """测试添加无效情感名称"""
        mapper = EmotionMapper()
        initial_count = len(mapper.get_available_emotions())

        # 添加无效的情感名称
        mapper.add_emotion("invalid_emotion", {"MouthSmile": 0.5})

        # 情感数量应该不变
        assert len(mapper.get_available_emotions()) == initial_count

    def test_add_emotion_case_insensitive(self):
        """测试情感名称不区分大小写"""
        mapper = EmotionMapper()

        # 添加大写的情感名称
        mapper.add_emotion("HAPPY", {"MouthSmile": 1.0})

        # 应该映射到正确的情感
        params = mapper.map_emotion(EmotionType.HAPPY)
        assert params["MouthSmile"] == 1.0


class TestEmotionMapperGetAvailableEmotions:
    """测试获取可用情感列表功能"""

    def test_get_available_emotions_returns_list(self):
        """测试返回列表类型"""
        mapper = EmotionMapper()
        emotions = mapper.get_available_emotions()

        assert isinstance(emotions, list)

    def test_get_available_emotions_contains_defaults(self):
        """测试包含所有默认情感"""
        mapper = EmotionMapper()
        emotions = mapper.get_available_emotions()

        assert EmotionType.NEUTRAL in emotions
        assert EmotionType.HAPPY in emotions
        assert EmotionType.SAD in emotions
        assert EmotionType.ANGRY in emotions
        assert EmotionType.SURPRISED in emotions
        assert EmotionType.LOVE in emotions
        assert EmotionType.SHY in emotions
        assert EmotionType.EXCITED in emotions
        assert EmotionType.CONFUSED in emotions
        assert EmotionType.SCARED in emotions

    def test_get_available_emotions_after_modification(self):
        """测试修改后的情感列表"""
        custom_map = {
            EmotionType.HAPPY: {"MouthSmile": 1.0},
        }
        mapper = EmotionMapper(custom_map)
        emotions = mapper.get_available_emotions()

        assert len(emotions) == 1
        assert EmotionType.HAPPY in emotions


class TestEmotionMapperDefaultEmotionMap:
    """测试默认情感映射"""

    def test_default_emotion_map_is_complete(self):
        """测试默认映射包含所有情感类型"""
        default_emotions = EmotionMapper.DEFAULT_EMOTION_MAP

        # 检查所有定义的情感类型
        all_emotion_types = [
            EmotionType.NEUTRAL,
            EmotionType.HAPPY,
            EmotionType.SAD,
            EmotionType.ANGRY,
            EmotionType.SURPRISED,
            EmotionType.LOVE,
            EmotionType.SHY,
            EmotionType.EXCITED,
            EmotionType.CONFUSED,
            EmotionType.SCARED,
        ]

        for emotion in all_emotion_types:
            assert emotion in default_emotions
            assert isinstance(default_emotions[emotion], dict)

    def test_default_emotion_map_parameters(self):
        """测试默认映射参数的合理性"""
        default_map = EmotionMapper.DEFAULT_EMOTION_MAP

        # 检查所有参数值在合理范围内 [-1.0, 2.0]（VTS参数可以超出[0,1]范围）
        for _emotion, params in default_map.items():
            for param_name, param_value in params.items():
                assert isinstance(param_name, str)
                assert isinstance(param_value, (int, float))
                assert -1.0 <= param_value <= 2.0, f"参数 {param_name} 的值 {param_value} 超出范围 [-1.0, 2.0]"

    def test_default_emotion_map_has_common_parameters(self):
        """测试默认映射包含常见的表情参数"""
        default_map = EmotionMapper.DEFAULT_EMOTION_MAP

        # 检查至少有一个情感包含这些常见参数
        common_params = ["MouthSmile", "EyeOpenLeft", "EyeOpenRight"]
        found_params = set()

        for params in default_map.values():
            for param in common_params:
                if param in params:
                    found_params.add(param)

        # 至少应该有一些常见参数
        assert len(found_params) > 0


class TestEmotionMapperEdgeCases:
    """测试边界情况"""

    def test_map_emotion_not_in_map(self):
        """测试映射未定义的情感（应返回 NEUTRAL）"""
        custom_map = {
            EmotionType.HAPPY: {"MouthSmile": 1.0},
        }
        mapper = EmotionMapper(custom_map)

        # 映射未定义的情感应返回默认的 NEUTRAL
        params = mapper.map_emotion(EmotionType.SAD)
        neutral_params = EmotionMapper.DEFAULT_EMOTION_MAP[EmotionType.NEUTRAL]

        assert params == neutral_params

    def test_map_emotion_with_empty_params(self):
        """测试映射空参数的情感"""
        mapper = EmotionMapper()
        mapper.set_emotion_mapping(EmotionType.HAPPY, {})

        params = mapper.map_emotion(EmotionType.HAPPY)
        assert params == {}

    def test_set_emotion_mapping_with_empty_params(self):
        """测试设置空的参数映射"""
        mapper = EmotionMapper()
        mapper.set_emotion_mapping(EmotionType.HAPPY, {})

        params = mapper.map_emotion(EmotionType.HAPPY)
        assert params == {}

    def test_multiple_mappers_independent(self):
        """测试多个 mapper 实例相互独立"""
        mapper1 = EmotionMapper()
        mapper2 = EmotionMapper()

        # 修改 mapper1
        mapper1.set_emotion_mapping(EmotionType.HAPPY, {"MouthSmile": 1.0})

        # mapper2 应该不受影响
        params1 = mapper1.map_emotion(EmotionType.HAPPY)
        params2 = mapper2.map_emotion(EmotionType.HAPPY)

        assert params1["MouthSmile"] == 1.0
        assert params2["MouthSmile"] == 0.8  # 默认值
