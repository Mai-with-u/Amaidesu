"""面捕合成器的通用通道：平台无关的身体参数名、取值范围、静止值与变化速度上限

每一帧是一组"通道名 → 值"，描述身体部位此刻的状态（头转了多少度、眼睛睁多开、
嘴角上扬多少），不认任何平台或模型的参数名；平台驱动负责换算成本平台的输入。

取值约定：
- 头部三轴单位是角度，正负方向沿用 VTS 面捕输入（FaceAngleX/Y/Z）的约定；
- 眼睛开合 0 = 闭上、1 = 正常睁眼；睁大另走 ``eye_wide``（0~1）；
- 眉、嘴角以 0 为中性、取 -1~1：眉 +1 高抬、-1 压低；嘴角 +1 上扬、-1 下垂；
- 视线取 -1~1，正方向与头部左右转同号——"看向某处"时头和眼朝同一边；
- 张嘴、嘴歪、鼓腮、生气按 0~1（嘴歪 -1~1）。
"""

from __future__ import annotations

from typing import Dict, Tuple

Frame = Dict[str, float]

HEAD_YAW = "head_yaw"
HEAD_PITCH = "head_pitch"
HEAD_ROLL = "head_roll"
EYE_OPEN_L = "eye_open_l"
EYE_OPEN_R = "eye_open_r"
EYE_WIDE = "eye_wide"
GAZE_X = "gaze_x"
GAZE_Y = "gaze_y"
BROW_L = "brow_l"
BROW_R = "brow_r"
MOUTH_SMILE = "mouth_smile"
MOUTH_OPEN = "mouth_open"
MOUTH_X = "mouth_x"
CHEEK_PUFF = "cheek_puff"
ANGRY = "angry"

# 表情配方里可用的两个双侧简写：同时作用左右眼 / 左右眉（左右不同的表情另写单侧通道）
EYE_OPEN = "eye_open"
BROW = "brow"

CHANNELS: Tuple[str, ...] = (
    HEAD_YAW,
    HEAD_PITCH,
    HEAD_ROLL,
    EYE_OPEN_L,
    EYE_OPEN_R,
    EYE_WIDE,
    GAZE_X,
    GAZE_Y,
    BROW_L,
    BROW_R,
    MOUTH_SMILE,
    MOUTH_OPEN,
    MOUTH_X,
    CHEEK_PUFF,
    ANGRY,
)

# 各通道取值范围：合成最后一步统一钳制，任何来源都写不出界
CHANNEL_LIMITS: Dict[str, Tuple[float, float]] = {
    HEAD_YAW: (-30.0, 30.0),
    HEAD_PITCH: (-20.0, 20.0),
    HEAD_ROLL: (-30.0, 30.0),
    EYE_OPEN_L: (0.0, 1.0),
    EYE_OPEN_R: (0.0, 1.0),
    EYE_WIDE: (0.0, 1.0),
    GAZE_X: (-1.0, 1.0),
    GAZE_Y: (-1.0, 1.0),
    BROW_L: (-1.0, 1.0),
    BROW_R: (-1.0, 1.0),
    MOUTH_SMILE: (-1.0, 1.0),
    MOUTH_OPEN: (0.0, 1.0),
    MOUTH_X: (-1.0, 1.0),
    CHEEK_PUFF: (0.0, 1.0),
    ANGRY: (0.0, 1.0),
}

# 每秒最多变化多少：脸不会瞬移。眼皮要快到 0.08 秒内闭上（眨眼），视线要容得下
# 眼跳（几十毫秒走完），头部、嘴角、眉这些"慢部位"限得紧，避免一帧切满
SLEW_PER_S: Dict[str, float] = {
    HEAD_YAW: 90.0,
    HEAD_PITCH: 90.0,
    HEAD_ROLL: 90.0,
    EYE_OPEN_L: 15.0,
    EYE_OPEN_R: 15.0,
    EYE_WIDE: 5.0,
    GAZE_X: 10.0,
    GAZE_Y: 10.0,
    BROW_L: 5.0,
    BROW_R: 5.0,
    MOUTH_SMILE: 3.0,
    MOUTH_OPEN: 12.0,
    MOUTH_X: 3.0,
    CHEEK_PUFF: 3.0,
    ANGRY: 3.0,
}


def rest_frame(rest_smile: float = 0.0) -> Frame:
    """人设静止姿态：头正、眼正常睁开、看镜头、眉中性，嘴角按人设基线。"""
    frame: Frame = {name: 0.0 for name in CHANNELS}
    frame[EYE_OPEN_L] = 1.0
    frame[EYE_OPEN_R] = 1.0
    frame[MOUTH_SMILE] = rest_smile
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


__all__ = [
    "ANGRY",
    "BROW",
    "BROW_L",
    "BROW_R",
    "CHANNELS",
    "CHANNEL_LIMITS",
    "CHEEK_PUFF",
    "EYE_OPEN",
    "EYE_OPEN_L",
    "EYE_OPEN_R",
    "EYE_WIDE",
    "Frame",
    "GAZE_X",
    "GAZE_Y",
    "HEAD_PITCH",
    "HEAD_ROLL",
    "HEAD_YAW",
    "MOUTH_OPEN",
    "MOUTH_SMILE",
    "MOUTH_X",
    "SLEW_PER_S",
    "clamp_frame",
    "limit_slew",
    "rest_frame",
]
