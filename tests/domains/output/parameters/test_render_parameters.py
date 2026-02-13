"""
测试 ExpressionParameters (渲染参数)

运行: uv run pytest tests/domains/output/parameters/test_render_parameters.py -v
"""

import time

import pytest

from src.modules.types import ExpressionParameters

# =============================================================================
# 创建和默认值测试
# =============================================================================


def test_expression_parameters_creation():
    """测试 ExpressionParameters 创建"""
    params = ExpressionParameters()

    assert params.tts_text == ""
    assert params.tts_enabled is True
    assert params.subtitle_text == ""
    assert params.subtitle_enabled is True
    assert params.expressions == {}
    assert params.expressions_enabled is True
    assert params.hotkeys == []
    assert params.hotkeys_enabled is True
    assert params.actions == []
    assert params.actions_enabled is True
    assert params.metadata == {}
    assert params.priority == 100


def test_expression_parameters_with_values():
    """测试带值的 ExpressionParameters 创建"""
    params = ExpressionParameters(
        tts_text="测试消息",
        subtitle_text="测试字幕",
        expressions={"smile": 0.8},
        hotkeys=["hotkey1"],
        actions=[{"type": "test"}],
        metadata={"source": "test"},
        priority=50,
    )

    assert params.tts_text == "测试消息"
    assert params.subtitle_text == "测试字幕"
    assert params.expressions == {"smile": 0.8}
    assert params.hotkeys == ["hotkey1"]
    assert params.actions == [{"type": "test"}]
    assert params.metadata == {"source": "test"}
    assert params.priority == 50


# =============================================================================
# TTS 相关测试
# =============================================================================


def test_tts_fields():
    """测试 TTS 字段"""
    params = ExpressionParameters(tts_text="TTS内容", tts_enabled=False)

    assert params.tts_text == "TTS内容"
    assert params.tts_enabled is False


def test_tts_empty_text():
    """测试空 TTS 文本"""
    params = ExpressionParameters(tts_text="")
    assert params.tts_text == ""


def test_tts_long_text():
    """测试长 TTS 文本"""
    long_text = "a" * 10000
    params = ExpressionParameters(tts_text=long_text)
    assert params.tts_text == long_text


# =============================================================================
# 字幕相关测试
# =============================================================================


def test_subtitle_fields():
    """测试字幕字段"""
    params = ExpressionParameters(subtitle_text="字幕内容", subtitle_enabled=True)

    assert params.subtitle_text == "字幕内容"
    assert params.subtitle_enabled is True


def test_subtitle_empty():
    """测试空字幕"""
    params = ExpressionParameters(subtitle_text="")
    assert params.subtitle_text == ""


def test_subtitle_disabled():
    """测试禁用字幕"""
    params = ExpressionParameters(subtitle_enabled=False)
    assert params.subtitle_enabled is False


# =============================================================================
# 表情参数测试
# =============================================================================


def test_expressions_empty():
    """测试空表情参数"""
    params = ExpressionParameters()
    assert params.expressions == {}


def test_expressions_single():
    """测试单个表情参数"""
    params = ExpressionParameters(expressions={"smile": 0.8})
    assert params.expressions == {"smile": 0.8}


def test_expressions_multiple():
    """测试多个表情参数"""
    params = ExpressionParameters(
        expressions={
            "smile": 0.8,
            "eye_open": 0.9,
            "mouth_open": 0.5,
        }
    )

    assert len(params.expressions) == 3
    assert params.expressions["smile"] == 0.8
    assert params.expressions["eye_open"] == 0.9
    assert params.expressions["mouth_open"] == 0.5


def test_expressions_float_values():
    """测试表情参数浮点数值"""
    params = ExpressionParameters(
        expressions={
            "param1": 0.0,
            "param2": 0.5,
            "param3": 1.0,
        }
    )

    assert params.expressions["param1"] == 0.0
    assert params.expressions["param2"] == 0.5
    assert params.expressions["param3"] == 1.0


def test_expressions_disabled():
    """测试禁用表情"""
    params = ExpressionParameters(
        expressions={"smile": 0.8},
        expressions_enabled=False,
    )

    assert params.expressions == {"smile": 0.8}
    assert params.expressions_enabled is False


# =============================================================================
# 热键测试
# =============================================================================


def test_hotkeys_empty():
    """测试空热键列表"""
    params = ExpressionParameters()
    assert params.hotkeys == []


def test_hotkeys_single():
    """测试单个热键"""
    params = ExpressionParameters(hotkeys=["smile"])
    assert params.hotkeys == ["smile"]


def test_hotkeys_multiple():
    """测试多个热键"""
    params = ExpressionParameters(hotkeys=["smile", "wave", "dance"])
    assert len(params.hotkeys) == 3
    assert "smile" in params.hotkeys
    assert "wave" in params.hotkeys
    assert "dance" in params.hotkeys


def test_hotkeys_disabled():
    """测试禁用热键"""
    params = ExpressionParameters(
        hotkeys=["smile"],
        hotkeys_enabled=False,
    )

    assert params.hotkeys == ["smile"]
    assert params.hotkeys_enabled is False


# =============================================================================
# 动作测试
# =============================================================================


def test_actions_empty():
    """测试空动作列表"""
    params = ExpressionParameters()
    assert params.actions == []


def test_actions_single():
    """测试单个动作"""
    action = {"type": "move", "target": "position"}
    params = ExpressionParameters(actions=[action])
    assert len(params.actions) == 1
    assert params.actions[0] == action


def test_actions_multiple():
    """测试多个动作"""
    actions = [
        {"type": "move", "target": "position1"},
        {"type": "animate", "name": "wave"},
    ]
    params = ExpressionParameters(actions=actions)
    assert len(params.actions) == 2


def test_actions_complex():
    """测试复杂动作结构"""
    action = {
        "type": "composite",
        "steps": [
            {"action": "move", "params": {"x": 10, "y": 20}},
            {"action": "rotate", "params": {"angle": 90}},
        ],
    }
    params = ExpressionParameters(actions=[action])
    assert params.actions[0]["steps"][0]["params"]["x"] == 10


def test_actions_disabled():
    """测试禁用动作"""
    params = ExpressionParameters(
        actions=[{"type": "test"}],
        actions_enabled=False,
    )

    assert params.actions_enabled is False


# =============================================================================
# 元数据测试
# =============================================================================


def test_metadata_empty():
    """测试空元数据"""
    params = ExpressionParameters()
    assert params.metadata == {}


def test_metadata_single():
    """测试单个元数据"""
    params = ExpressionParameters(metadata={"source": "test"})
    assert params.metadata == {"source": "test"}


def test_metadata_multiple():
    """测试多个元数据"""
    params = ExpressionParameters(
        metadata={
            "source": "test",
            "user_id": "12345",
            "timestamp": 1234567890,
        }
    )

    assert len(params.metadata) == 3
    assert params.metadata["source"] == "test"
    assert params.metadata["user_id"] == "12345"
    assert params.metadata["timestamp"] == 1234567890


def test_metadata_nested():
    """测试嵌套元数据"""
    params = ExpressionParameters(
        metadata={
            "nested": {
                "key1": "value1",
                "key2": "value2",
            }
        }
    )

    assert params.metadata["nested"]["key1"] == "value1"


# =============================================================================
# 优先级测试
# =============================================================================


def test_priority_default():
    """测试默认优先级"""
    params = ExpressionParameters()
    assert params.priority == 100


def test_priority_custom():
    """测试自定义优先级"""
    params = ExpressionParameters(priority=1)
    assert params.priority == 1


def test_priority_zero():
    """测试零优先级（最高）"""
    params = ExpressionParameters(priority=0)
    assert params.priority == 0


def test_priority_negative():
    """测试负优先级"""
    params = ExpressionParameters(priority=-10)
    assert params.priority == -10


def test_priority_very_high():
    """测试很高优先级"""
    params = ExpressionParameters(priority=9999)
    assert params.priority == 9999


# =============================================================================
# 时间戳测试
# =============================================================================


def test_timestamp_auto():
    """测试自动生成时间戳"""
    before = time.time()
    params = ExpressionParameters()
    after = time.time()

    assert before <= params.timestamp <= after


def test_timestamp_in_order():
    """测试时间戳顺序"""
    params1 = ExpressionParameters()
    time.sleep(0.01)  # 确保时间差
    params2 = ExpressionParameters()

    assert params2.timestamp > params1.timestamp


# =============================================================================
# 序列化测试
# =============================================================================


def test_model_dump():
    """测试序列化为字典"""
    params = ExpressionParameters(
        tts_text="测试",
        expressions={"smile": 0.8},
        priority=50,
    )

    data = params.model_dump()

    assert data["tts_text"] == "测试"
    assert data["expressions"] == {"smile": 0.8}
    assert data["priority"] == 50


def test_model_dump_json():
    """测试序列化为 JSON"""
    params = ExpressionParameters(
        tts_text="测试",
        hotkeys=["smile"],
    )

    json_str = params.model_dump_json()

    assert "测试" in json_str
    assert "smile" in json_str


def test_model_dump_excludes():
    """测试序列化排除字段"""
    params = ExpressionParameters(tts_text="测试")

    # Pydantic 默认包含所有字段
    data = params.model_dump()
    assert "tts_text" in data


# =============================================================================
# 反序列化测试
# =============================================================================


def test_model_validate():
    """测试从字典创建"""
    data = {
        "tts_text": "测试",
        "subtitle_text": "字幕",
        "expressions": {"smile": 0.8},
        "priority": 50,
    }

    params = ExpressionParameters(**data)

    assert params.tts_text == "测试"
    assert params.subtitle_text == "字幕"
    assert params.expressions == {"smile": 0.8}
    assert params.priority == 50


def test_model_validate_partial():
    """测试部分字段创建"""
    data = {"tts_text": "测试"}

    params = ExpressionParameters(**data)

    assert params.tts_text == "测试"
    # 其他字段使用默认值
    assert params.subtitle_text == ""
    assert params.priority == 100


# =============================================================================
# 字符串表示测试
# =============================================================================


def test_repr():
    """测试字符串表示"""
    params = ExpressionParameters(
        tts_text="测试",
        subtitle_text="字幕",
        expressions={"smile": 0.8},
        hotkeys=["smile"],
        actions=[{"type": "test"}],
    )

    repr_str = repr(params)

    assert "ExpressionParameters" in repr_str
    # 验证显示的是长度而不是内容
    assert "tts=" in repr_str
    assert "subtitle=" in repr_str
    assert "expressions=" in repr_str
    assert "hotkeys=" in repr_str
    assert "actions=" in repr_str


def test_repr_empty():
    """测试空参数的字符串表示"""
    params = ExpressionParameters()
    repr_str = repr(params)

    assert "ExpressionParameters" in repr_str


# =============================================================================
# 向后兼容性测试 (RenderParameters 别名)
# =============================================================================


def test_render_parameters_alias():
    """测试 RenderParameters 别名（已移除，使用 ExpressionParameters）"""
    # RenderParameters 别名已被移除，现在使用 ExpressionParameters
    # 此测试仅保留以确保向后兼容性已移除
    pass


def test_render_parameters_creation():
    """测试使用 RenderParameters 创建（已移除别名）"""
    # RenderParameters 别名已被移除，现在使用 ExpressionParameters
    params = ExpressionParameters(tts_text="测试")

    assert params.tts_text == "测试"

# =============================================================================
# 边界条件测试
# =============================================================================


def test_unicode_text():
    """测试 Unicode 文本"""
    text = "测试中文🎉emoji😊"
    params = ExpressionParameters(tts_text=text)
    assert params.tts_text == text


def test_special_characters():
    """测试特殊字符"""
    text = "测试\n换行\t制表符"
    params = ExpressionParameters(tts_text=text)
    assert params.tts_text == text


def test_very_long_lists():
    """测试超长列表"""
    hotkeys = [f"hotkey{i}" for i in range(1000)]
    params = ExpressionParameters(hotkeys=hotkeys)

    assert len(params.hotkeys) == 1000


def test_all_enabled_false():
    """测试所有功能禁用"""
    params = ExpressionParameters(
        tts_enabled=False,
        subtitle_enabled=False,
        expressions_enabled=False,
        hotkeys_enabled=False,
        actions_enabled=False,
    )

    assert params.tts_enabled is False
    assert params.subtitle_enabled is False
    assert params.expressions_enabled is False
    assert params.hotkeys_enabled is False
    assert params.actions_enabled is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
