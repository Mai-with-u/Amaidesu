"""Warudo 蓝图生成：入口动作名与适配器发出的名字一一对齐、两种写法映射、角色引用取场景"""

import json

import pytest

from src.modules.avatar.platform.warudo.blueprint_builder import (
    DEFAULT_CHARACTER_ID,
    HEAD_MOTIONS,
    POSES,
    build_blueprints,
    find_scene_character,
)
from src.modules.avatar.platform.warudo.expression_profile import (
    MAIMAI_PROFILE_PATH,
    WarudoExpressionConfig,
    read_profile_expression,
)
from src.modules.avatar.platform.warudo.warudo_provider import BUILTIN_MOTIONS


def _value(node: dict, key: str):
    return json.loads(node["dataInputs"][key]["value"])


def _entries(blueprint: dict) -> dict:
    """{动作名: 该入口驱动的节点}"""
    nodes = blueprint["nodes"]
    targets = {c["outputNode"]: nodes[c["inputNode"]] for c in blueprint["flowConnections"]}
    return {_value(n, "Action"): targets[nid] for nid, n in nodes.items() if n["name"] == "ON_WEBSOCKET_ACTION"}


def test_vrm_mode_listens_to_every_name_the_adapter_sends() -> None:
    blueprints = build_blueprints(WarudoExpressionConfig(), mode="vrm")
    assert set(blueprints) == {
        "Amaidesu 表情.json",
        "Amaidesu 口型.json",
        "Amaidesu 眨眼与移眼.json",
        "Amaidesu 头部动作与站姿.json",
    }
    expression = _entries(blueprints["Amaidesu 表情.json"])
    assert set(expression) == {"happy", "angry", "sad", "relaxed", "surprised"}
    assert _value(expression["happy"], "UseVRMBlendShapeProxy") is True
    # 代理方式不过渡：Warudo 起不了代理的补间，带过渡每写一次都会报补间失败
    assert _value(expression["happy"], "TransitionTime") == 0.0

    mouth = _entries(blueprints["Amaidesu 口型.json"])
    assert {k: _value(v, "BlendShape") for k, v in mouth.items()}["VowelA"] == "aa"  # 元音 → VRM 口型名
    eyes = _entries(blueprints["Amaidesu 眨眼与移眼.json"])
    assert (_value(eyes["eye_close"], "BlendShape"), _value(eyes["eye_shift_up"], "BlendShape")) == ("blink", "lookUp")

    motions = _entries(blueprints["Amaidesu 头部动作与站姿.json"])
    assert set(motions) == set(BUILTIN_MOTIONS) == set(HEAD_MOTIONS) | set(POSES)  # 与适配器内置条目同一批名字
    assert _value(motions["pose_think"], "Character")["id"] == DEFAULT_CHARACTER_ID


def test_vrm0_mode_uses_the_legacy_preset_names() -> None:
    blueprints = build_blueprints(WarudoExpressionConfig(), mode="vrm0")
    expression = {k: _value(v, "BlendShape") for k, v in _entries(blueprints["Amaidesu 表情.json"]).items()}
    # 动作名仍是适配器发的 VRM 1.0 名，蓝图里写 0.x 预设名（Warudo 对 0.x 模型只认这套）
    assert (expression["happy"], expression["sad"], expression["relaxed"]) == ("Joy", "Sorrow", "Fun")
    mouth = {k: _value(v, "BlendShape") for k, v in _entries(blueprints["Amaidesu 口型.json"]).items()}
    eyes = {k: _value(v, "BlendShape") for k, v in _entries(blueprints["Amaidesu 眨眼与移眼.json"]).items()}
    assert (mouth["VowelA"], eyes["eye_close"], eyes["eye_shift_left"]) == ("A", "Blink", "LookLeft")
    with pytest.raises(ValueError, match="vrm2"):
        build_blueprints(WarudoExpressionConfig(), mode="vrm2")


def test_shape_mode_writes_model_shapes_and_weights_flow_from_the_action() -> None:
    profile = WarudoExpressionConfig.from_dict(read_profile_expression(MAIMAI_PROFILE_PATH))
    blueprints = build_blueprints(profile, mode="shape")
    expression = blueprints["Amaidesu 表情.json"]
    entries = _entries(expression)
    assert set(entries) == set(profile.expression_keys())
    node = entries["mouth_simle_teeth"]
    assert _value(node, "BlendShape") == "mouth_simle_teeth" and _value(node, "UseVRMBlendShapeProxy") is False
    assert _value(node, "TransitionTime") == 0.2  # 直接写形状保留 0.2 秒过渡
    assert {c["outputPort"] for c in expression["dataConnections"]} == {"FloatData"}  # 权重取自动作数据
    eyes = _entries(blueprints["Amaidesu 眨眼与移眼.json"])
    assert _value(eyes["eye_shift_left"], "BlendShape") == "eye_shift_left"  # 形状名方式按同名形状写


def test_character_reference_comes_from_the_scene_file(tmp_path) -> None:
    scene = tmp_path / "scene.json"
    scene.write_text(
        json.dumps(
            {
                "assets": [
                    {"typeId": "6a05ecf3-1501-4cab-b9d7-84131b881a29", "name": "Camera 1", "id": "cam"},
                    {"typeId": "726ab674-a550-474e-8b92-66526a5ad55e", "name": "麦麦", "id": "char-1"},
                ]
            }
        ),
        encoding="utf-8",
    )
    character = find_scene_character(scene)
    assert character == {"id": "char-1", "name": "麦麦"}
    blueprints = build_blueprints(WarudoExpressionConfig(), mode="vrm", character=character)
    happy = _entries(blueprints["Amaidesu 表情.json"])["happy"]
    assert _value(happy, "Character") == character

    scene.write_text(json.dumps({"assets": None}), encoding="utf-8")
    assert find_scene_character(scene) is None
