"""Warudo 蓝图生成：身体动作入口覆盖合成器的全部动作且按类别播放、可换动画，风格化标记入口与驱动发的动作名对齐、
两种写法、角色引用取场景"""

import json

import pytest

from src.modules.avatar.platform.warudo.blueprint_builder import (
    DEFAULT_CHARACTER_ID,
    build_blueprints,
    find_scene_character,
)
from src.modules.avatar.platform.warudo.body_animations import ANIMATION_ROOT, body_action
from src.modules.avatar.platform.warudo.tracking_driver import marker_action
from src.modules.avatar.tracking.body import KINDS, BodyCue

BODY = "Amaidesu 身体动作.json"


def _value(node: dict, key: str):
    return json.loads(node["dataInputs"][key]["value"])


def _entries(blueprint: dict) -> dict:
    """{动作名: 该入口驱动的节点}"""
    nodes = blueprint["nodes"]
    targets = {c["outputNode"]: nodes[c["inputNode"]] for c in blueprint["flowConnections"]}
    return {_value(n, "Action"): targets[nid] for nid, n in nodes.items() if n["name"] == "ON_WEBSOCKET_ACTION"}


def test_body_entries_always_cover_every_body_action_and_markers_only_when_registered() -> None:
    assert set(build_blueprints({})) == {BODY}
    entries = _entries(build_blueprints({})[BODY])
    every = {body_action(BodyCue(kind, name)) for kind, names in KINDS.items() for name in names}
    assert set(entries) == every  # 合成器的每个身体动作都有入口，名字与驱动发的一致
    stance, overlay, gesture, wave = (
        entries["body_stance_hands_on_hips"],
        entries["body_overlay_sway"],
        entries["body_gesture_cheer"],
        entries["body_gesture_wave"],
    )
    assert stance["name"] == "PLAY_CHARACTER_IDLE_ANIMATION"  # 站姿：换待机动画，一直保持
    assert _value(stance, "Animation") == f"{ANIMATION_ROOT}/01_Idles/AGIA_Idle_angry_01_hands_on_waist"
    assert _value(overlay, "Additive") is True and _value(gesture, "Additive") is False  # 叠加 / 整段播一次
    # 挥手：换成循环动画（过渡和手势一样快），挥够了由驱动切回站姿
    assert wave["name"] == "PLAY_CHARACTER_IDLE_ANIMATION"
    assert _value(wave, "TransitionTime") < _value(stance, "TransitionTime")
    assert _value(stance, "Character")["id"] == DEFAULT_CHARACTER_ID


def test_body_animation_overrides_accept_relative_or_full_paths() -> None:
    body = {"fist_up": "01_Idles/AGIA_Idle_energetic_02_right_hand_piece", "wave": "character-animation://x/y"}
    entries = _entries(build_blueprints({}, body=body)[BODY])
    assert _value(entries["body_stance_fist_up"], "Animation") == (
        f"{ANIMATION_ROOT}/01_Idles/AGIA_Idle_energetic_02_right_hand_piece"
    )
    assert _value(entries["body_gesture_wave"], "Animation") == "character-animation://x/y"
    with pytest.raises(ValueError, match="未知的身体动作"):
        build_blueprints({}, body={"backflip": "x"})


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
