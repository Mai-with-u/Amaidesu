"""MessageType 登记表单元测试。

覆盖：
- 7 个内置类型登记完整性
- 重复登记报错
- require_message_type 未登记报错
- get_message_type / list_message_types 查询 API
- clear() / reset_to_builtins() 测试钩子
"""

from __future__ import annotations

import pytest

from src.modules.types.message_type import (
    MESSAGE_TYPE_REGISTRY,
    MessageTypeNotRegistered,
    MessageTypeRegistrationError,
    MessageTypeSpec,
    clear,
    get_message_type,
    list_message_types,
    register_message_type,
    require_message_type,
    reset_to_builtins,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """每个测试结束后恢复 7 个内置类型登记，确保测试隔离。

    使用 ``reset_to_builtins()``（模块内置辅助函数）而非 ``importlib.reload``——
    后者会重新创建模块 namespace 中的 ``MESSAGE_TYPE_REGISTRY`` dict 对象，
    导致测试模块 import-time 的引用脱钩。
    """
    yield
    reset_to_builtins()


# ==================== 内置类型登记完整性 ====================


def test_seven_builtin_types_registered():
    """模块 import 后 7 个内置类型必须全部登记。"""
    keys = sorted(MESSAGE_TYPE_REGISTRY.keys())
    assert keys == ["enter", "game.text", "gift", "guard", "super_chat", "text", "unknown"]


def test_builtin_specs_have_required_fields():
    """每个内置 spec 必填字段非空。"""
    for name, spec in MESSAGE_TYPE_REGISTRY.items():
        assert spec.name == name
        assert spec.label, f"{name} 必须有 label"
        assert spec.prompt_template, f"{name} 必须有 prompt_template"
        # default_importance 默认 0.5，且在 [0,1]
        assert 0.0 <= spec.default_importance <= 1.0


# ==================== 模板字节级一致 ====================


def test_prompt_template_text_byte_identical_to_current_behavior():
    """text 类型 prompt 模板渲染结果应与 message_buffer 现状一致：`昵称: 内容`。"""
    spec = require_message_type("text")
    rendered = spec.prompt_template.format(text="hello", nickname="千石可乐")
    assert rendered == "千石可乐: hello"


def test_prompt_template_gift_byte_identical():
    """gift 类型 prompt 模板应与现状一致：`[礼物] 昵称: 内容`。"""
    spec = require_message_type("gift")
    rendered = spec.prompt_template.format(text="送出了 1 个辣条", nickname="千石可乐")
    assert rendered == "[礼物] 千石可乐: 送出了 1 个辣条"


def test_prompt_template_super_chat_byte_identical():
    """super_chat 类型 prompt 模板应与现状一致：`[醒目留言] 昵称: 内容`。"""
    spec = require_message_type("super_chat")
    rendered = spec.prompt_template.format(text="感谢主播", nickname="路人甲")
    assert rendered == "[醒目留言] 路人甲: 感谢主播"


def test_prompt_template_guard_byte_identical():
    """guard 类型 prompt 模板应与现状一致：`[上舰] 昵称: 内容`。"""
    spec = require_message_type("guard")
    rendered = spec.prompt_template.format(text="开通了总督", nickname="舰长A")
    assert rendered == "[上舰] 舰长A: 开通了总督"


def test_prompt_template_enter_byte_identical():
    """enter 类型 prompt 模板应与现状一致：`[入场] 昵称: 内容`。"""
    spec = require_message_type("enter")
    rendered = spec.prompt_template.format(text="进入了直播间", nickname="新人")
    assert rendered == "[入场] 新人: 进入了直播间"


def test_prompt_template_game_text_no_nickname():
    """game.text 类型 prompt 模板应与 source_prefix 特判输出一致：`[游戏] 内容`（无昵称）。"""
    spec = require_message_type("game.text")
    rendered = spec.prompt_template.format(text="游戏剧情台词", nickname="文字冒险游戏")
    assert rendered == "[游戏] 游戏剧情台词"


def test_display_template_gift_substitutes_defaults():
    """widget gift display 模板：默认值由调用方 resolve，模板只描述结构。"""
    spec = require_message_type("gift")
    # 调用方负责 resolve 默认值（对齐 widget 现状：gift_name or '礼物', gift_count or 1）
    rendered = spec.display_template.format(gift_name="辣条", gift_count=5)
    assert rendered == "送出 辣条 x5"
    rendered_default = spec.display_template.format(gift_name="礼物", gift_count=1)
    assert rendered_default == "送出 礼物 x1"


def test_display_template_super_chat_substitutes():
    """widget super_chat display 模板：sc_price 和 sc_message 由调用方 resolve。"""
    spec = require_message_type("super_chat")
    rendered = spec.display_template.format(sc_price=50, sc_message="支持主播")
    assert rendered == "¥50 支持主播"


def test_display_template_guard_substitutes():
    """widget guard display 模板：guard_name 由调用方从 guard_level 解析后传入。"""
    spec = require_message_type("guard")
    rendered = spec.display_template.format(guard_name="总督")
    assert rendered == "开通了 总督"


def test_display_template_enter_literal():
    """widget enter display 模板：字面常量。"""
    spec = require_message_type("enter")
    assert spec.display_template == "进入了直播间"


# ==================== require_message_type 拦截 ====================


def test_require_message_type_unregistered_raises():
    """未登记类型抛 MessageTypeNotRegistered，且错误信息含类型名。"""
    with pytest.raises(MessageTypeNotRegistered) as exc_info:
        require_message_type("not_registered_xyz")
    assert "not_registered_xyz" in str(exc_info.value)


def test_require_message_type_registered_returns_spec():
    """已登记类型返回对应的 MessageTypeSpec 实例。"""
    spec = require_message_type("text")
    assert isinstance(spec, MessageTypeSpec)
    assert spec.name == "text"
    assert spec.label == "消息"


# ==================== 重复登记 ====================


def test_register_message_type_duplicate_raises():
    """重复登记同名类型抛 MessageTypeRegistrationError。"""
    with pytest.raises(MessageTypeRegistrationError) as exc_info:

        @register_message_type(name="text", label="dup-text", prompt_template="{text}")
        class DupText:
            pass

    assert "text" in str(exc_info.value)


def test_register_message_type_different_names_ok():
    """不同名字的登记可并存。"""

    @register_message_type(name="__test_custom__", label="测试", prompt_template="{text}")
    class CustomType:
        pass


    assert "__test_custom__" in MESSAGE_TYPE_REGISTRY


# ==================== 查询 API ====================


def test_get_message_type_returns_none_for_unregistered():
    """get_message_type 未登记返回 None，不抛错。"""
    assert get_message_type("not_exist") is None


def test_get_message_type_returns_spec_for_registered():
    """get_message_type 已登记返回 spec。"""
    spec = get_message_type("gift")
    assert spec is not None
    assert spec.name == "gift"


def test_list_message_types_returns_copy():
    """list_message_types 返回副本，外部修改不影响内部注册表。"""
    snapshot = list_message_types()
    snapshot["__bogus__"] = MessageTypeSpec(name="__bogus__", label="bogus", prompt_template="{text}")
    assert "__bogus__" not in MESSAGE_TYPE_REGISTRY


def test_list_message_types_includes_all_builtins():
    """list_message_types 包含全部 7 个内置类型。"""
    snapshot = list_message_types()
    assert len(snapshot) == 7
    assert set(snapshot.keys()) == {"text", "gift", "super_chat", "guard", "enter", "unknown", "game.text"}


# ==================== clear() 测试钩子 ====================


def test_clear_empties_registry():
    """clear() 后注册表为空。"""
    clear()
    assert MESSAGE_TYPE_REGISTRY == {}


def test_decorator_sets_backreference():
    """被装饰类获得 _registered_message_type 反向引用。"""

    @register_message_type(name="__test_br__", label="反向引用测试", prompt_template="{text}")
    class BackRefType:
        pass


    assert BackRefType._registered_message_type == "__test_br__"  # type: ignore[attr-defined]