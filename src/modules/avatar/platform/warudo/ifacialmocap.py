"""iFacialMocap 协议编码：把面捕合成器的一帧写成 iPhone 面捕 App 发给电脑的那种 UDP 包

iFacialMocap 是 iPhone 上的面捕 App：按苹果 ARKit 抓脸，把每帧数据经 UDP 发到电脑（默认
端口 49983）。Warudo、VSeeFace、VNyan 都自带它的接收器，拿到的是 ARKit 的 52 个表情系数
加头部、眼球转角，再走各自的面捕流程（标定、平滑、映射到模型的形状）驱动角色。面捕合成器
给出的正是同一套东西，按这个格式发出去，Warudo 就把面捕合成器当成一部在抓脸的 iPhone。

包是一行文本，各段以 ``|`` 分隔：

- 表情系数 ``名字-整数``，0~100。分左右的 ARKit 名把结尾的 Left / Right 写成 ``_L`` / ``_R``
  （``eyeBlink_L``）；``jawLeft``、``mouthLeft`` 这类说的是方向而不是哪一侧，名字照旧；
- 头部 ``=head#俯仰,左右转,歪头,x,y,z``：前三个是转角（度）；后三个是头部位置，单位按
  ARKit 的习惯是米（协议原文没写单位，样例数值是几厘米的量级）；
- 眼球 ``rightEye#俯仰,左右,0``、``leftEye#俯仰,左右,0``，单位为度。

头部位置装的是身体位置：真人用 iPhone 面捕时，身子往前凑、往后缩、左右挪，手机看到的就是
头在平移；Warudo 收到头部位移会按自己的设置带动身体（面捕设置里的 Body Movement
Intensity）。面捕合成器的身体前倾后缩（表情、心情带出来的）就这样落到 Warudo 的身体上；
身体跟着头转不用发，Warudo 按头部转角自己做（Body Rotation Type）。

方向按 ARKit 的右手坐标系：俯仰正数是低头 / 往下看；左右正数是转向画面右边（和面捕合成器
同号）；歪头正数是头顶倒向画面左边；位置 x 正数是画面右边、y 正数是往上、z 正数是靠近镜头
（和面捕合成器的身体位置同号）。面捕合成器的俯仰正数是抬头，所以俯仰取反。
"""

from __future__ import annotations

from typing import Mapping

from src.modules.avatar.tracking.channels import (
    ARKIT_BLENDSHAPES,
    BODY_X,
    BODY_Y,
    BODY_Z,
    GAZE_X,
    GAZE_Y,
    HEAD_PITCH,
    HEAD_ROLL,
    HEAD_YAW,
)

# iFacialMocap 的默认接收端口
DEFAULT_PORT = 49983

# 视线 ±1 对应的眼球转角（度）：左右约 25 度、上下约 20 度，和真人眼球的常用转动范围相当
GAZE_YAW_DEG = 25.0
GAZE_PITCH_DEG = 20.0
# 身体位置 ±1 对应的头部平移（米）：左右、前后各 10 厘米，上下 5 厘米，约是人坐着
# 能挪动范围的一半，和 VTS 驱动取输入范围一半的做法一致；整体强弱在 Warudo 里再调
BODY_POSITION_M = (0.10, 0.05, 0.10)

# 方向型的名字（往哪边，而不是哪一侧），不改写成 _L / _R
_DIRECTIONAL = frozenset({"jawLeft", "jawRight", "mouthLeft", "mouthRight"})


def wire_name(name: str) -> str:
    """ARKit 系数名 → iFacialMocap 包里的名字（eyeBlinkLeft → eyeBlink_L）。"""
    if name in _DIRECTIONAL:
        return name
    if name.endswith("Left"):
        return name[: -len("Left")] + "_L"
    if name.endswith("Right"):
        return name[: -len("Right")] + "_R"
    return name


_WIRE_NAMES = {name: wire_name(name) for name in ARKIT_BLENDSHAPES}


def _numbers(*values: float) -> str:
    # 加 0.0 把取反得到的 -0.0 变回 0.0，包里不出现 "-0.000"；位置按米写到毫米
    return ",".join(f"{value + 0.0:.3f}" for value in values)


def encode_frame(frame: Mapping[str, float]) -> bytes:
    """一帧规范通道 → 一个 iFacialMocap 数据包（52 个表情系数 + 头部转角与位置 + 两只眼球）。"""
    get = frame.get
    parts = [f"{wire}-{round(min(1.0, max(0.0, get(name, 0.0))) * 100)}" for name, wire in _WIRE_NAMES.items()]
    angles = (-get(HEAD_PITCH, 0.0), get(HEAD_YAW, 0.0), -get(HEAD_ROLL, 0.0))
    scale_x, scale_y, scale_z = BODY_POSITION_M
    position = (get(BODY_X, 0.0) * scale_x, get(BODY_Y, 0.0) * scale_y, get(BODY_Z, 0.0) * scale_z)
    parts.append("=head#" + _numbers(*angles, *position))
    eye = _numbers(-get(GAZE_Y, 0.0) * GAZE_PITCH_DEG, get(GAZE_X, 0.0) * GAZE_YAW_DEG, 0.0)
    parts.append(f"rightEye#{eye}")
    parts.append(f"leftEye#{eye}")
    return ("|".join(parts) + "|").encode("ascii")


__all__ = ["BODY_POSITION_M", "DEFAULT_PORT", "GAZE_PITCH_DEG", "GAZE_YAW_DEG", "encode_frame", "wire_name"]
