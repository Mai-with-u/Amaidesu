"""面捕合成器的通道：按面部捕捉的通行规范描述一帧"脸和身体此刻的样子"

每一帧是一组"通道名 → 值"，不认任何平台或模型的参数名；平台驱动负责换算成本平台
的输入。词表直接用业界规范，而不是自造一套：

- **表情系数**：苹果 ARKit 定义的 52 个（名字原样照搬，取值 0~1）。iPhone 面捕、
  VTS 的 iPhone 输入、VBridger、Warudo 与 VSeeFace 的"完美同步"、VRChat 面捕都用它
  或它的扩展，模型按规范接好这些，换平台时不用重新约定；
- **口型**：VRM 的 5 个元音口型 aa / ih / ou / ee / oh（0~1），说话时由口型分析器给出；
- **姿态**：头部与身体的三轴角度（度）、身体位置（-1~1）、视线（-1~1）。

方向约定：头部三轴沿用 VTS 面捕输入（FaceAngleX/Y/Z）的正负；视线与头部左右转同号，
正方向是画面右边、上边，"看向某处"时头和眼朝同一边；ARKit 名字里的 Left / Right 指
角色自己的左右。身体位置 z 正数是靠近镜头（前倾）。
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional, Tuple

Frame = Dict[str, float]

HEAD_YAW = "head_yaw"
HEAD_PITCH = "head_pitch"
HEAD_ROLL = "head_roll"
BODY_YAW = "body_yaw"
BODY_PITCH = "body_pitch"
BODY_ROLL = "body_roll"
BODY_X = "body_x"
BODY_Y = "body_y"
BODY_Z = "body_z"
GAZE_X = "gaze_x"
GAZE_Y = "gaze_y"

POSE_CHANNELS: Tuple[str, ...] = (
    HEAD_YAW,
    HEAD_PITCH,
    HEAD_ROLL,
    BODY_YAW,
    BODY_PITCH,
    BODY_ROLL,
    BODY_X,
    BODY_Y,
    BODY_Z,
    GAZE_X,
    GAZE_Y,
)

# ARKit 的 52 个表情系数，按部位分组、组内顺序同苹果文档
ARKIT_BLENDSHAPES: Tuple[str, ...] = (
    # 眼
    "eyeBlinkLeft",
    "eyeLookDownLeft",
    "eyeLookInLeft",
    "eyeLookOutLeft",
    "eyeLookUpLeft",
    "eyeSquintLeft",
    "eyeWideLeft",
    "eyeBlinkRight",
    "eyeLookDownRight",
    "eyeLookInRight",
    "eyeLookOutRight",
    "eyeLookUpRight",
    "eyeSquintRight",
    "eyeWideRight",
    # 下巴
    "jawForward",
    "jawLeft",
    "jawRight",
    "jawOpen",
    # 嘴
    "mouthClose",
    "mouthFunnel",
    "mouthPucker",
    "mouthLeft",
    "mouthRight",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    # 眉
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    # 脸颊
    "cheekPuff",
    "cheekSquintLeft",
    "cheekSquintRight",
    # 鼻
    "noseSneerLeft",
    "noseSneerRight",
    # 舌
    "tongueOut",
)

# VRM 的元音口型
VISEMES: Tuple[str, ...] = ("aa", "ih", "ou", "ee", "oh")

CHANNELS: Tuple[str, ...] = (*POSE_CHANNELS, *ARKIT_BLENDSHAPES, *VISEMES)

# 眼球朝向的 8 个系数由视线换算（合成器最后一步写入），配方与动作不直接写
EYE_LOOK: Tuple[str, ...] = tuple(name for name in ARKIT_BLENDSHAPES if name.startswith("eyeLook"))

# 双侧简写：ARKit 名去掉 Left / Right 即同时作用左右（配方里左右对称时少写一半）。
# 下巴、嘴整体的左右平移（jawLeft / mouthLeft）是方向而不是两侧，不做简写
BILATERAL: Dict[str, Tuple[str, str]] = {
    name[: -len("Left")]: (name, name[: -len("Left")] + "Right")
    for name in ARKIT_BLENDSHAPES
    if name.endswith("Left") and name not in EYE_LOOK and name not in ("jawLeft", "mouthLeft")
}

# 各通道取值范围：合成最后一步统一钳制，任何来源都写不出界
_POSE_LIMITS: Dict[str, Tuple[float, float]] = {
    HEAD_YAW: (-30.0, 30.0),
    HEAD_PITCH: (-20.0, 20.0),
    HEAD_ROLL: (-30.0, 30.0),
    BODY_YAW: (-20.0, 20.0),
    BODY_PITCH: (-15.0, 15.0),
    BODY_ROLL: (-15.0, 15.0),
    BODY_X: (-1.0, 1.0),
    BODY_Y: (-1.0, 1.0),
    BODY_Z: (-1.0, 1.0),
    GAZE_X: (-1.0, 1.0),
    GAZE_Y: (-1.0, 1.0),
}
CHANNEL_LIMITS: Dict[str, Tuple[float, float]] = {
    **_POSE_LIMITS,
    **{name: (0.0, 1.0) for name in (*ARKIT_BLENDSHAPES, *VISEMES)},
}


def _slew(name: str) -> Optional[float]:
    """每秒最多变化多少：眼皮要快到 0.08 秒内闭上（眨眼），下巴与口型跟得上说话，
    嘴角、脸颊这些"慢部位"限得紧，避免一帧切满；眼球朝向随视线换算，不单独限速。"""
    if name in (HEAD_YAW, HEAD_PITCH, HEAD_ROLL):
        return 90.0
    if name in (BODY_YAW, BODY_PITCH, BODY_ROLL):
        return 30.0
    if name in (BODY_X, BODY_Y, BODY_Z):
        return 3.0
    if name in (GAZE_X, GAZE_Y):
        return 10.0
    if name in EYE_LOOK:
        return None
    if name.startswith("eyeBlink"):
        return 15.0
    if name in VISEMES or name in ("jawOpen", "mouthClose"):
        return 12.0
    if name.startswith(("mouthFunnel", "mouthPucker", "mouthStretch", "mouthRoll", "mouthUpperUp", "mouthLowerDown")):
        return 8.0
    if name.startswith(("eyeWide", "eyeSquint", "brow", "mouthShrug", "mouthPress", "mouthDimple", "jaw", "tongue")):
        return 5.0
    return 3.0


SLEW_PER_S: Dict[str, float] = {name: rate for name in CHANNELS if (rate := _slew(name)) is not None}


def expand(offsets: Mapping[str, float]) -> Frame:
    """把双侧简写展开成左右两个通道（同名累加），其余原样保留。"""
    out: Frame = {}
    for key, value in offsets.items():
        for name in BILATERAL.get(key, (key,)):
            out[name] = out.get(name, 0.0) + value
    return out


def rest_frame(rest_smile: float = 0.0) -> Frame:
    """人设静止姿态：头和身体正、看镜头、所有表情系数归零，嘴角按人设基线（负数是撇嘴）。"""
    frame: Frame = {name: 0.0 for name in CHANNELS}
    side = "Smile" if rest_smile >= 0 else "Frown"
    frame[f"mouth{side}Left"] = frame[f"mouth{side}Right"] = abs(rest_smile)
    return clamp_frame(frame)


def clamp_frame(frame: Frame) -> Frame:
    """把每个已知通道钳制到取值范围内（未知通道原样保留，留给驱动自行忽略）。"""
    out: Frame = dict(frame)
    for name, (low, high) in CHANNEL_LIMITS.items():
        if name in out:
            out[name] = min(high, max(low, out[name]))
    return out


def limit_slew(previous: Frame, target: Frame, dt_ms: int) -> Frame:
    """按每通道速度上限，从上一帧向目标帧走一步（dt 超过 200 毫秒按 200 计，卡顿后不瞬移）。"""
    dt_s = min(max(dt_ms, 0), 200) / 1000.0
    out: Frame = dict(target)
    for name, value in target.items():
        rate = SLEW_PER_S.get(name)
        if rate is None or name not in previous:
            continue
        step = rate * dt_s
        last = previous[name]
        out[name] = min(last + step, max(last - step, value))
    return out


def eye_look_from_gaze(gaze_x: float, gaze_y: float) -> Frame:
    """视线 → ARKit 的 8 个眼球朝向系数。看向画面右边 = 角色自己的左边：左眼朝外、右眼朝内。"""
    right, left = max(gaze_x, 0.0), max(-gaze_x, 0.0)
    up, down = max(gaze_y, 0.0), max(-gaze_y, 0.0)
    return {
        "eyeLookOutLeft": right,
        "eyeLookInRight": right,
        "eyeLookInLeft": left,
        "eyeLookOutRight": left,
        "eyeLookUpLeft": up,
        "eyeLookUpRight": up,
        "eyeLookDownLeft": down,
        "eyeLookDownRight": down,
    }


# ===== 常用合成量：把规范系数读成平台常见的单个输入（驱动与观察共用） =====


def _sides(side: Optional[str]) -> Tuple[str, ...]:
    return ("Left", "Right") if side is None else (side,)


def smile(frame: Mapping[str, float], side: Optional[str] = None) -> float:
    """嘴角：笑减撇，-1（下垂）~ 1（上扬）；不给 side 取左右平均。"""
    sides = _sides(side)
    return sum(frame.get(f"mouthSmile{s}", 0.0) - frame.get(f"mouthFrown{s}", 0.0) for s in sides) / len(sides)


def eye_openness(frame: Mapping[str, float], side: str) -> float:
    """睁眼程度：0 闭上、1 正常睁开（睁大另看 eyeWide）。"""
    return 1.0 - frame.get(f"eyeBlink{side}", 0.0)


def brow_height(frame: Mapping[str, float], side: str) -> float:
    """眉高：外侧上提与眉心上提抬高、压眉降低，-1 ~ 1。"""
    up = 0.6 * frame.get(f"browOuterUp{side}", 0.0) + 0.4 * frame.get("browInnerUp", 0.0)
    return max(-1.0, min(1.0, up - frame.get(f"browDown{side}", 0.0)))


__all__ = [
    "ARKIT_BLENDSHAPES",
    "BILATERAL",
    "BODY_PITCH",
    "BODY_ROLL",
    "BODY_X",
    "BODY_Y",
    "BODY_YAW",
    "BODY_Z",
    "CHANNELS",
    "CHANNEL_LIMITS",
    "EYE_LOOK",
    "Frame",
    "GAZE_X",
    "GAZE_Y",
    "HEAD_PITCH",
    "HEAD_ROLL",
    "HEAD_YAW",
    "POSE_CHANNELS",
    "SLEW_PER_S",
    "VISEMES",
    "brow_height",
    "clamp_frame",
    "expand",
    "eye_look_from_gaze",
    "eye_openness",
    "limit_slew",
    "rest_frame",
    "smile",
]
