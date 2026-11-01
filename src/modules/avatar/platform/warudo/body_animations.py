"""Warudo 上的身体动作：面捕合成器的身体动作名 → Warudo 自带的人形动画

Warudo 自带一套人形动画（AGIA）：带情绪的待机站姿、叠加在站姿上的局部动作、一次性的动作，
任何人形模型都能直接用。站姿用"切换待机动画"播放（一直保持到下一个站姿），叠加动作
叠在当前站姿上播一次，手势整段播一次后回到站姿。蓝图经 WebSocket 动作 ``body_<类别>_<名字>``
接收（数据 1），蓝图生成与驱动共用这里的名字。

某个动作想换成别的动画，在 ``[platform.warudo.body_animations]`` 按动作名登记即可（可以只写
AGIA 下的相对路径），改完重新生成并导入蓝图。
"""

from __future__ import annotations

from typing import Dict, Mapping, Tuple

from src.modules.avatar.tracking.body import GESTURE, OVERLAY, STANCE, BodyCue, check_action_map, kind_of

ANIMATION_ROOT = "character-animation://resources/Animations/AGIA"

# (类别, 动作名) → AGIA 下的相对路径
_DEFAULTS: Dict[Tuple[str, str], str] = {
    (STANCE, "standing"): "01_Idles/AGIA_Idle_generic_01",
    (STANCE, "hands_front"): "01_Idles/AGIA_Idle_cute_01_hands_on_front",
    (STANCE, "fist_up"): "01_Idles/AGIA_Idle_energetic_01_right_fist_up",
    (STANCE, "lean_in"): "01_Idles/AGIA_Idle_cute_03_leaning_forward",
    (STANCE, "hand_on_hip"): "01_Idles/AGIA_Idle_classy_01_left_hand_on_waist",
    (STANCE, "hands_clasped"): "01_Idles/AGIA_Idle_calm_02_hands_on_front",
    (STANCE, "scratch_head"): "01_Idles/AGIA_Idle_pitiable_01_right_hand_on_back_head",
    (STANCE, "worried"): "01_Idles/AGIA_Idle_concern_01_right_hand_front",
    (STANCE, "crying"): "01_Idles/AGIA_Idle_cry",
    (STANCE, "hands_on_hips"): "01_Idles/AGIA_Idle_angry_01_hands_on_waist",
    (STANCE, "refuse"): "01_Idles/AGIA_Idle_deny_01",
    (STANCE, "hands_open"): "01_Idles/AGIA_Idle_surprise_01_hands_open_front",
    (STANCE, "head_in_hands"): "01_Idles/AGIA_Idle_stress_01_hands_on_back_head",
    (STANCE, "puzzled"): "01_Idles/AGIA_Idle_what_01",
    (STANCE, "hands_behind"): "01_Idles/AGIA_Idle_calm_01_hands_on_back",
    (STANCE, "facepalm"): "01_Idles/AGIA_Idle_fedup_02_right_hand_on_face",
    (STANCE, "slouch"): "01_Idles/AGIA_Idle_fedup_01_slouching",
    (STANCE, "thinking"): "01_Idles/AGIA_Idle_think_01",
    (OVERLAY, "sway"): "02_Layers/AGIA_Layer_swing_body_01",
    (OVERLAY, "laugh"): "02_Layers/AGIA_Layer_laugh_up_01",
    (GESTURE, "wave"): "03_Others/AGIA_Other_wave_hand_01",
    (GESTURE, "wave_both"): "03_Others/AGIA_Other_wave_hands_01",
    (GESTURE, "point"): "03_Others/AGIA_Other_point_finger_01_emote",
    (GESTURE, "cheer"): "03_Others/AGIA_Other_enegetic_02",
}
# 待机动画重置时回到的站姿
DEFAULT_IDLE = f"{ANIMATION_ROOT}/{_DEFAULTS[(STANCE, 'standing')]}"


def body_action(cue: BodyCue) -> str:
    """身体动作在蓝图通道里的动作名。"""
    return f"body_{cue.kind}_{cue.name}"


def check_overrides(overrides: Mapping[str, str]) -> Dict[str, str]:
    """校验配置里的动画替换：动作名要认识、动画不能为空；返回去掉首尾空白的副本。"""
    return check_action_map(overrides, what="动画")


def resolve(overrides: Mapping[str, str]) -> Dict[BodyCue, str]:
    """生效的动作 → 动画完整地址（默认表 + 配置替换；替换可以只写 AGIA 下的相对路径）。"""
    table = {BodyCue(kind, name): path for (kind, name), path in _DEFAULTS.items()}
    for name, path in overrides.items():
        table[BodyCue(kind_of(name), name)] = path
    return {cue: path if "://" in path else f"{ANIMATION_ROOT}/{path}" for cue, path in table.items()}


__all__ = ["ANIMATION_ROOT", "DEFAULT_IDLE", "body_action", "check_overrides", "resolve"]
