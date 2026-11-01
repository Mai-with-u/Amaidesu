"""Warudo 蓝图生成：按配置生成可直接导入 Warudo 的蓝图 JSON

蓝图是 Warudo 场景里的节点图。适配器经 WebSocket 发 ``{"action": 名字, "data": 值}``，
蓝图里的"收到 WebSocket 动作"节点按名字接住，再驱动角色。脸、眼、头走面捕数据
（iFacialMocap 接收器），不经蓝图；蓝图只管两样：

- 身体动作：面捕合成器的站姿、叠加动作、手势各一个入口（``body_<类别>_<名字>``），播放
  Warudo 自带的人形动画，与模型无关；
- 风格化标记：配置里登记的每个标记一个入口（``marker_<名字>``），把收到的 0~1 写到模型
  上对应的形状（或走 VRM 表情代理写同名的自定义表情）。

手画的蓝图把形状名、场景资源写死在节点里，换模型就得重画，所以按配置生成。角色引用
默认取 Warudo 新建场景里固定的"角色 1"资源 ID；传入场景文件时改取场景里第一个角色。
视线、抛鱼这类依赖场景道具或第三方插件的蓝图不在生成范围。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.modules.avatar.tracking.body import GESTURE, OVERLAY, STANCE, BodyCue

from . import body_animations
from .tracking_driver import marker_action

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

# 风格化标记的写法：shape = 直接写模型网格上的形状；vrm = 走 VRM 表情代理写同名的自定义表情
MODES = ("shape", "vrm")

# 站姿切换、手势起落的过渡秒数：站姿全身都动，过渡慢一点才不突兀
_STANCE_TRANSITION_S = 1.0
_ONE_SHOT_TRANSITION_S = 0.4

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
    """每个 (动作名, 表情/形状名) 一个入口：收到 0~1 → 写入（直接写形状时 0.2 秒过渡）"""
    graph = _Graph(name, character)
    # 走 VRM 表情代理时 Warudo 起不了过渡补间（实测值能写上，但每次写都在日志里报一次补间失败），
    # 代理方式改为不过渡、直接写
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
        # 收到动作 → 用动作携带的值写形状
        graph.flow(entry, setter)
        graph.data(entry, "FloatData", setter, "Value")
    return graph


def _play_idle(graph: _Graph, animation: str, row: int, *, transition: float = _STANCE_TRANSITION_S) -> str:
    """切换待机动画（站姿、循环播放的手势）：换上后一直循环，直到下一次切换。"""
    return graph.node(
        _PLAY_IDLE_ANIMATION,
        "PLAY_CHARACTER_IDLE_ANIMATION",
        {
            "Character": graph.character_input(),
            "Animation": _value("string", animation),
            "TransitionTime": _value("float", transition),
            "TransitionEasing": _value("DG.Tweening.Ease", _EASE_OUT_CUBIC),
            "RestartIfPlaying": _value("bool", False),
            "ResetIfPlaying": _value("bool", False),
            "ResetToAnimation": _value("string", body_animations.DEFAULT_IDLE),
            "ResetRootMotion": _value("bool", True),
        },
        420.0,
        row * 260.0,
    )


def _play_once(graph: _Graph, animation: str, row: int, *, additive: bool) -> str:
    """播一次：叠加动作叠在当前站姿上（additive），手势整段播完回到站姿。"""
    return graph.node(
        _PLAY_ONE_SHOT_ANIMATION,
        "PLAY_CHARACTER_ONE_SHOT_ANIMATION",
        {
            "Character": graph.character_input(),
            "Animation": _value("string", animation),
            "TransitionTime": _value("float", _ONE_SHOT_TRANSITION_S),
            "Weight": _value("float", 1.0),
            "Speed": _value("float", 1.0),
            "Masked": _value("bool", False),
            "MaskedBodyParts": {
                "type": "Warudo.Plugins.Core.Assets.Character.CharacterAsset+AnimationMaskedBodyPart[]",
                "value": "[]",
            },
            "Additive": _value("bool", additive),
        },
        420.0,
        row * 260.0,
    )


def _body_graph(character: Dict[str, str], animations: Mapping[BodyCue, str]) -> _Graph:
    """身体动作：站姿、叠加动作、手势各一个入口（按类别排，同类按名字排）。

    站姿换待机动画；挥手类手势也换待机动画（循环挥），过渡和手势一样快，挥够了由驱动切回站姿；
    其余手势与叠加动作播一次。
    """
    graph = _Graph("Amaidesu 身体动作", character)
    order = {STANCE: 0, OVERLAY: 1, GESTURE: 2}
    for row, (cue, animation) in enumerate(sorted(animations.items(), key=lambda i: (order[i[0].kind], i[0].name))):
        entry = graph.on_action(body_animations.body_action(cue), "Integer", row)
        if cue.kind == STANCE:
            player = _play_idle(graph, animation, row)
        elif cue in body_animations.LOOPED:
            player = _play_idle(graph, animation, row, transition=_ONE_SHOT_TRANSITION_S)
        else:
            player = _play_once(graph, animation, row, additive=cue.kind == OVERLAY)
        graph.flow(entry, player)
    return graph


def find_scene_character(scene_path: Path) -> Optional[Dict[str, str]]:
    """从 Warudo 场景文件取第一个角色资源的 ID 与名字；没有角色返回 None"""
    with scene_path.open(encoding="utf-8") as fh:
        scene = json.load(fh)
    for asset in scene.get("assets") or []:
        if isinstance(asset, dict) and asset.get("typeId") == _CHARACTER_ASSET_TYPE_ID:
            return {"id": str(asset["id"]), "name": str(asset.get("name", ""))}
    return None


def build_blueprints(
    markers: Mapping[str, str],
    *,
    mode: str = "shape",
    character: Optional[Dict[str, str]] = None,
    body: Optional[Mapping[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """生成蓝图：{文件名: 蓝图 dict}。身体动作总有；登记了风格化标记才有标记蓝图。

    Args:
        markers: 风格化标记名 → 模型上的形状名（与适配器读的是同一份配置，动作名天然对齐）
        mode: 标记的写法，shape = 直接写同名形状；vrm = 走 VRM 表情代理写同名的自定义表情
        character: 角色资源引用 {id, name}；缺省用新建场景的"角色 1"
        body: 身体动作的动画替换（动作名 → 动画），与适配器读的是同一份配置
    """
    if mode not in MODES:
        raise ValueError(f"未知写法 {mode!r}，可选 {MODES}")
    ref = character or {"id": DEFAULT_CHARACTER_ID, "name": "角色 1"}
    graphs: List[_Graph] = [_body_graph(ref, body_animations.resolve(body or {}))]
    if markers:
        targets: Iterable[Tuple[str, str]] = [(marker_action(name), shape) for name, shape in markers.items()]
        graphs.append(_blendshape_graph("Amaidesu 风格化标记", ref, targets, vrm=mode == "vrm"))
    return {f"{graph.name}.json": graph.to_dict() for graph in graphs}


__all__ = ["DEFAULT_CHARACTER_ID", "MODES", "build_blueprints", "find_scene_character"]
