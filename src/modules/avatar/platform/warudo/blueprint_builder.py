"""Warudo 蓝图生成：按表情档案生成可直接导入 Warudo 的蓝图 JSON

蓝图是 Warudo 场景里的节点图。适配器经 WebSocket 发 ``{"action": 名字, "data": 值}``，
蓝图里的"收到 WebSocket 动作"节点按名字接住，再驱动角色。手画的蓝图把形状名、
场景资源写死在节点里，换模型就得重画；这里改为按档案生成：

- 表情：档案里的每个表情键一个入口，写同名表情（VRM 方式走 VRM 表情代理，任何
  VRM 模型通用，0.x 与 1.0 的预设名不同需分开选；形状名方式直接写模型网格上的
  形状，用于麦麦这类自定义模型）；
- 口型、眨眼、移眼：固定协议键，按方式映射到 VRM 预设名或同名形状；
- 头部动作与站姿：播放 Warudo 自带动画，与模型无关。

角色引用默认取 Warudo 新建场景里固定的"角色 1"资源 ID；传入场景文件时改取场景
里第一个角色。视线、抛鱼这类依赖场景道具或第三方插件的蓝图不在生成范围。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .expression_profile import BLINK_KEY, EYE_SHIFT_KEYS, VOWEL_KEYS, WarudoExpressionConfig

# Warudo 新建场景里"角色 1"的固定资源 ID（不同安装的默认场景一致）
DEFAULT_CHARACTER_ID = "29399e49-1c26-41bb-b40f-294dc0590f68"
_CHARACTER_ASSET_TYPE_ID = "726ab674-a550-474e-8b92-66526a5ad55e"

# 节点类型 ID（取自 Warudo 导出的蓝图）
_ON_WEBSOCKET_ACTION = "61445859-8471-4baa-88d6-b186773bd40d"
_SET_CHARACTER_BLENDSHAPE = "979f57cc-4329-4e21-8b99-ddde1d72a0ea"
_PLAY_ONE_SHOT_ANIMATION = "3c8f47ba-5430-4061-8506-19812b4d2ec0"
_PLAY_IDLE_ANIMATION = "ca6ade7e-27b5-4344-b04a-b373c8b9c50e"

# WebSocket 动作数据类型（Warudo 枚举值）
_DATA_TYPES = {"Integer": 2, "Float": 3}

# 固定协议键 → VRM 1.0 标准表情名（VRM 方式用；形状名方式按同名形状写）
_VRM_FIXED_NAMES = {
    "VowelA": "aa",
    "VowelI": "ih",
    "VowelU": "ou",
    "VowelE": "ee",
    "VowelO": "oh",
    BLINK_KEY: "blink",
    "eye_shift_left": "lookLeft",
    "eye_shift_right": "lookRight",
    "eye_shift_up": "lookUp",
    "eye_shift_down": "lookDown",
}
# 形状名方式下口型元音对应的形状（VRM 模型网格上的口型形状通常同名）
_SHAPE_VOWEL_NAMES = {"VowelA": "aa", "VowelI": "ih", "VowelU": "ou", "VowelE": "ee", "VowelO": "oh"}

# VRM 0.x 模型在 Warudo 表情代理里用旧版预设名（Warudo 日志列出的可用表情实测如此）：
# VRM 1.0 标准名 → VRM 0.x 预设名。0.x 没有"惊讶"预设，取 VRoid 导出常见的自定义名 Surprised
_VRM0_NAMES = {
    "neutral": "Neutral",
    "happy": "Joy",
    "angry": "Angry",
    "sad": "Sorrow",
    "relaxed": "Fun",
    "surprised": "Surprised",
    "aa": "A",
    "ih": "I",
    "ou": "U",
    "ee": "E",
    "oh": "O",
    "blink": "Blink",
    "blinkLeft": "Blink_L",
    "blinkRight": "Blink_R",
    "lookUp": "LookUp",
    "lookDown": "LookDown",
    "lookLeft": "LookLeft",
    "lookRight": "LookRight",
}

# 生成方式：vrm = VRM 1.0 表情代理；vrm0 = VRM 0.x 表情代理；shape = 直接写同名形状
MODES = ("vrm", "vrm0", "shape")

_ANIMATION_ROOT = "character-animation://resources/Animations/AGIA"
_IDLE_DEFAULT = f"{_ANIMATION_ROOT}/01_Idles/AGIA_Idle_generic_01"

# 头部动作：动作名 → (动画, 权重, 速度)；歪头沿用原蓝图调过的轻幅慢速
HEAD_MOTIONS: Dict[str, Tuple[str, float, float]] = {
    "nod_once": (f"{_ANIMATION_ROOT}/02_Layers/AGIA_Layer_nod_once_01", 1.0, 1.0),
    "nod_twice": (f"{_ANIMATION_ROOT}/02_Layers/AGIA_Layer_nod_twice_01", 1.0, 1.0),
    "shake_head": (f"{_ANIMATION_ROOT}/02_Layers/AGIA_Layer_shake_head_01", 1.0, 1.0),
    "tilt_head": (f"{_ANIMATION_ROOT}/02_Layers/AGIA_Layer_tilt_neck_01", 0.1, 0.27),
    "look_away": (f"{_ANIMATION_ROOT}/02_Layers/AGIA_Layer_look_away_01", 1.0, 1.0),
}
# 站姿：动作名 → 待机动画（切换后一直保持，直到下一个站姿）
POSES: Dict[str, str] = {
    "pose_calm": f"{_ANIMATION_ROOT}/01_Idles/AGIA_Idle_calm_01_hands_on_back",
    "pose_think": f"{_ANIMATION_ROOT}/01_Idles/AGIA_Idle_think_01",
    "pose_default": _IDLE_DEFAULT,
}

_EASE_OUT_CUBIC = {"label": "OutCubic", "value": 9, "description": None, "icon": None}


def _value(type_name: str, value: Any) -> Dict[str, Any]:
    """节点输入值：Warudo 把每个值再 JSON 编码一层存成字符串"""
    return {"type": type_name, "value": json.dumps(value, ensure_ascii=False)}


class _Graph:
    """单个蓝图的节点与连线收集器"""

    def __init__(self, name: str, character: Dict[str, str]) -> None:
        self.name = name
        self.character = character
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.data_connections: List[Dict[str, str]] = []
        self.flow_connections: List[Dict[str, str]] = []

    def node(self, type_id: str, name: str, inputs: Dict[str, Dict[str, Any]], x: float, y: float) -> str:
        node_id = str(uuid.uuid4())
        self.nodes[node_id] = {"id": node_id, "dataInputs": inputs, "typeId": type_id, "name": name, "x": x, "y": y}
        return node_id

    def on_action(self, action: str, data_type: str, row: int) -> str:
        """一个"收到 WebSocket 动作"入口节点"""
        info = f'发送 {{ "action": "{action}", "data": ... }} 到 Warudo 的 WebSocket 端口即可触发（Amaidesu 生成）'
        return self.node(
            _ON_WEBSOCKET_ACTION,
            "ON_WEBSOCKET_ACTION",
            {
                "Info": _value("string", info),
                "Action": _value("string", action),
                "DataType": {
                    "type": "Warudo.Plugins.Core.Nodes.External.WebSocketActionDataType",
                    "value": json.dumps(
                        {"label": data_type, "value": _DATA_TYPES[data_type], "description": None, "icon": None}
                    ),
                },
            },
            0.0,
            row * 260.0,
        )

    def flow(self, source: str, target: str, source_port: str = "Exit") -> None:
        self.flow_connections.append(
            {"outputNode": source, "inputNode": target, "outputPort": source_port, "inputPort": "Enter"}
        )

    def data(self, source: str, source_port: str, target: str, target_port: str) -> None:
        self.data_connections.append(
            {"outputNode": source, "inputNode": target, "outputPort": source_port, "inputPort": target_port}
        )

    def character_input(self) -> Dict[str, Any]:
        return {
            "type": "Warudo.Plugins.Core.Assets.Character.CharacterAsset",
            "value": json.dumps(self.character, ensure_ascii=False),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "enabled": True,
            "name": self.name,
            "order": 0,
            "group": None,
            "panningX": 0.0,
            "panningY": 0.0,
            "scaling": 0.5,
            "nodes": self.nodes,
            "dataConnections": self.data_connections,
            "flowConnections": self.flow_connections,
            "properties": {
                "id": str(uuid.uuid4()),
                "dataInputs": {"Variables": {"type": "Warudo.Core.Graphs.GraphVariable[]", "value": "[]"}},
            },
        }


def _blendshape_graph(name: str, character: Dict[str, str], targets: Iterable[Tuple[str, str]], vrm: bool) -> _Graph:
    """每个 (动作名, 表情/形状名) 一个入口：收到权重 → 写入（直接写形状时 0.2 秒过渡）"""
    graph = _Graph(name, character)
    # 走 VRM 表情代理时 Warudo 起不了过渡补间（实测值能写上，但每次写都在日志里报一次补间失败，
    # 说话口型每秒写十来次会刷屏），代理方式改为不过渡、直接写
    transition = 0.0 if vrm else 0.2
    for row, (action, target) in enumerate(targets):
        entry = graph.on_action(action, "Float", row)
        setter = graph.node(
            _SET_CHARACTER_BLENDSHAPE,
            "SET_CHARACTER_BLENDSHAPE",
            {
                "Character": graph.character_input(),
                "ApplyToAllSkinnedMeshes": _value("bool", True),
                "TargetSkinnedMesh": _value("string", "Body"),
                "UseVRMBlendShapeProxy": _value("bool", vrm),
                "BlendShape": _value("string", target),
                "Value": _value("float", 0.0),
                "TransitionTime": _value("float", transition),
                "TransitionEasing": _value("DG.Tweening.Ease", _EASE_OUT_CUBIC),
            },
            420.0,
            row * 260.0,
        )
        # 收到动作 → 用动作携带的权重写表情
        graph.flow(entry, setter)
        graph.data(entry, "FloatData", setter, "Value")
    return graph


def _motion_graph(character: Dict[str, str]) -> _Graph:
    """头部动作（叠加播放一次）与站姿（切换待机动画）"""
    graph = _Graph("Amaidesu 头部动作与站姿", character)
    row = 0
    for action, (animation, weight, speed) in HEAD_MOTIONS.items():
        entry = graph.on_action(action, "Integer", row)
        player = graph.node(
            _PLAY_ONE_SHOT_ANIMATION,
            "PLAY_CHARACTER_ONE_SHOT_ANIMATION",
            {
                "Character": graph.character_input(),
                "Animation": _value("string", animation),
                "TransitionTime": _value("float", 1.2),
                "Weight": _value("float", weight),
                "Speed": _value("float", speed),
                "Masked": _value("bool", True),
                "MaskedBodyParts": {
                    "type": "Warudo.Plugins.Core.Assets.Character.CharacterAsset+AnimationMaskedBodyPart[]",
                    "value": json.dumps([{"label": "头", "value": 2, "description": None, "icon": None}]),
                },
                "Additive": _value("bool", True),
            },
            420.0,
            row * 260.0,
        )
        graph.flow(entry, player)
        row += 1
    for action, animation in POSES.items():
        entry = graph.on_action(action, "Integer", row)
        player = graph.node(
            _PLAY_IDLE_ANIMATION,
            "PLAY_CHARACTER_IDLE_ANIMATION",
            {
                "Character": graph.character_input(),
                "Animation": _value("string", animation),
                "TransitionTime": _value("float", 1.2),
                "TransitionEasing": _value("DG.Tweening.Ease", _EASE_OUT_CUBIC),
                "RestartIfPlaying": _value("bool", False),
                "ResetIfPlaying": _value("bool", False),
                "ResetToAnimation": _value("string", _IDLE_DEFAULT),
                "ResetRootMotion": _value("bool", True),
            },
            420.0,
            row * 260.0,
        )
        graph.flow(entry, player)
        row += 1
    return graph


def find_scene_character(scene_path: Path) -> Optional[Dict[str, str]]:
    """从 Warudo 场景文件取第一个角色资源的 ID 与名字；没有角色返回 None"""
    with scene_path.open(encoding="utf-8") as fh:
        scene = json.load(fh)
    for asset in scene.get("assets") or []:
        if isinstance(asset, dict) and asset.get("typeId") == _CHARACTER_ASSET_TYPE_ID:
            return {"id": str(asset["id"]), "name": str(asset.get("name", ""))}
    return None


def _target_name(key: str, mode: str) -> str:
    """动作名 → 蓝图里要写的表情/形状名"""
    if mode == "shape":
        # 形状名方式：元音写 aa/ih/ou/ee/oh 形状，其余键与形状同名
        return _SHAPE_VOWEL_NAMES.get(key, key)
    name = _VRM_FIXED_NAMES.get(key, key)
    # VRM 0.x：标准名换成旧版预设名，非标准键（模型自定义表情）原样保留
    return _VRM0_NAMES.get(name, name) if mode == "vrm0" else name


def build_blueprints(
    expression: WarudoExpressionConfig,
    *,
    mode: str = "vrm",
    character: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """按档案生成四个蓝图：{文件名: 蓝图 dict}

    Args:
        expression: 表情档案（与适配器读的是同一份配置，键名天然对齐）
        mode: vrm = 走 VRM 1.0 表情代理；vrm0 = 走 VRM 0.x 表情代理；shape = 直接写同名形状
        character: 角色资源引用 {id, name}；缺省用新建场景的"角色 1"
    """
    if mode not in MODES:
        raise ValueError(f"未知生成方式 {mode!r}，可选 {MODES}")
    ref = character or {"id": DEFAULT_CHARACTER_ID, "name": "角色 1"}
    proxy = mode != "shape"

    def targets(keys: Iterable[str]) -> List[Tuple[str, str]]:
        return [(key, _target_name(key, mode)) for key in keys]

    graphs = [
        _blendshape_graph("Amaidesu 表情", ref, targets(expression.expression_keys()), proxy),
        _blendshape_graph("Amaidesu 口型", ref, targets(VOWEL_KEYS), proxy),
        _blendshape_graph("Amaidesu 眨眼与移眼", ref, targets((BLINK_KEY, *EYE_SHIFT_KEYS)), proxy),
        _motion_graph(ref),
    ]
    return {f"{graph.name}.json": graph.to_dict() for graph in graphs}


__all__ = ["DEFAULT_CHARACTER_ID", "HEAD_MOTIONS", "MODES", "POSES", "build_blueprints", "find_scene_character"]
