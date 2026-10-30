"""Warudo 蓝图生成：动作入口与适配器内置条目同名、风格化标记入口与驱动发的动作名对齐、两种写法、角色引用取场景"""

import json

import pytest

from src.modules.avatar.platform.warudo.blueprint_builder import (
    DEFAULT_CHARACTER_ID,
    HEAD_MOTIONS,
    POSES,
    build_blueprints,
    find_scene_character,
)
from src.modules.avatar.platform.warudo.tracking_driver import marker_action
from src.modules.avatar.platform.warudo.warudo_provider import BUILTIN_MOTIONS


def _value(node: dict, key: str):
    return json.loads(node["dataInputs"][key]["value"])


def _entries(blueprint: dict) -> dict:
    """{动作名: 该入口驱动的节点}"""
    nodes = blueprint["nodes"]
    targets = {c["outputNode"]: nodes[c["inputNode"]] for c in blueprint["flowConnections"]}
    return {_value(n, "Action"): targets[nid] for nid, n in nodes.items() if n["name"] == "ON_WEBSOCKET_ACTION"}


def test_motions_always_and_markers_only_when_registered() -> None:
    assert set(build_blueprints({})) == {"Amaidesu 头部动作与站姿.json"}
    motions = _entries(build_blueprints({})["Amaidesu 头部动作与站姿.json"])
    assert set(motions) == set(BUILTIN_MOTIONS) == set(HEAD_MOTIONS) | set(POSES)  # 与适配器内置条目同一批名字
    assert _value(motions["pose_think"], "Character")["id"] == DEFAULT_CHARACTER_ID


def test_marker_entries_write_the_registered_shapes_with_the_driver_action_names() -> None:
    blueprints = build_blueprints({"tears": "Tears", "blush": "cheek_red"})
    markers = blueprints["Amaidesu 风格化标记.json"]
    entries = _entries(markers)
    assert set(entries) == {marker_action("tears"), marker_action("blush")}
    node = entries[marker_action("blush")]
    assert _value(node, "BlendShape") == "cheek_red" and _value(node, "UseVRMBlendShapeProxy") is False
    assert _value(node, "TransitionTime") == 0.2  # 直接写形状保留 0.2 秒过渡
    assert {c["outputPort"] for c in markers["dataConnections"]} == {"FloatData"}  # 值取自动作数据

    proxy = _entries(build_blueprints({"tears": "Tears"}, mode="vrm")["Amaidesu 风格化标记.json"])
    # 代理方式不过渡：Warudo 起不了代理的补间，带过渡每写一次都会报补间失败
    assert _value(proxy[marker_action("tears")], "UseVRMBlendShapeProxy") is True
    assert _value(proxy[marker_action("tears")], "TransitionTime") == 0.0
    with pytest.raises(ValueError, match="vrm0"):
        build_blueprints({}, mode="vrm0")


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
    blueprints = build_blueprints({"tears": "Tears"}, character=character)
    tears = _entries(blueprints["Amaidesu 风格化标记.json"])[marker_action("tears")]
    assert _value(tears, "Character") == character

    scene.write_text(json.dumps({"assets": None}), encoding="utf-8")
    assert find_scene_character(scene) is None
