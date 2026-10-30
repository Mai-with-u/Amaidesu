"""VRChat 面捕驱动：眼睛走 VRChat 官方的 OSC 眼动输入，脸走 VRCFaceTracking 的通行参数名

VRChat 的 OSC 能写的只有两类：官方的眼动输入（任何形象都认），和形象作者自定义的参数。

- **眼睛**（任何形象）：``/tracking/eye/EyesClosedAmount``（闭眼程度 0~1，两眼一起）与
  ``/tracking/eye/CenterPitchYaw``（俯仰、左右，度；俯仰正数往下、左右正数往形象自己的右边）。
  直播镜头通常在形象正前方，画面右边是形象自己的左边，所以左右取反。超过 10 秒没收到，
  VRChat 交还自带的自动眨眼与眼神，每帧都发。
- **脸**（做了面捕参数的形象）：VRChat 圈里给形象接面捕的事实标准是 VRCFaceTracking，参数名是它的
  "统一表情"第二版（``/avatar/parameters/FT/v2/JawOpen`` 这类）。按它的简化参数把 ARKit 系数
  换算过去，只发变化了的值；形象上没有的参数 VRChat 直接忽略。
- **风格化标记**：形象作者另做的参数（如 ``Tears``），按登记的名字写 0~1，变化了才发。

头部转动 VRChat 不开放写入，跟着玩家自己的头（桌面模式下是视角）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from src.modules.avatar.tracking.channels import GAZE_X, GAZE_Y, MARKER_PREFIX, Frame, smile

# 视线 ±1 对应的眼球转角（度），与其他平台一致
GAZE_YAW_DEG = 25.0
GAZE_PITCH_DEG = 20.0
# 面捕参数与标记：变化小于这个值不重发
SEND_EPSILON = 0.01

EYES_CLOSED = "/tracking/eye/EyesClosedAmount"
EYE_PITCH_YAW = "/tracking/eye/CenterPitchYaw"


def _avg(frame: Frame, name: str) -> float:
    return (frame.get(f"{name}Left", 0.0) + frame.get(f"{name}Right", 0.0)) / 2.0


def face_parameters(frame: Frame) -> Dict[str, float]:
    """ARKit 系数 → VRCFaceTracking 统一表情第二版的简化参数（名字不带前缀）。"""
    f = frame.get

    def lid(side: str) -> float:
        # 0 闭、0.75 正常睁开、1 睁大
        return 0.75 * (1.0 - f(f"eyeBlink{side}", 0.0)) + 0.25 * f(f"eyeWide{side}", 0.0)

    out: Dict[str, float] = {
        "JawOpen": f("jawOpen", 0.0),
        "MouthClosed": f("mouthClose", 0.0),
        "JawX": f("jawRight", 0.0) - f("jawLeft", 0.0),
        "MouthX": f("mouthRight", 0.0) - f("mouthLeft", 0.0),
        "LipFunnel": f("mouthFunnel", 0.0),
        "LipPucker": f("mouthPucker", 0.0),
        "MouthUpperUp": _avg(frame, "mouthUpperUp"),
        "MouthLowerDown": _avg(frame, "mouthLowerDown"),
        "BrowInnerUp": f("browInnerUp", 0.0),
        "BrowOuterUp": _avg(frame, "browOuterUp"),
        "CheekPuffSuck": f("cheekPuff", 0.0),
        "CheekSquint": _avg(frame, "cheekSquint"),
        "NoseSneer": _avg(frame, "noseSneer"),
        "TongueOut": f("tongueOut", 0.0),
    }
    for side in ("Left", "Right"):
        out[f"EyeLid{side}"] = lid(side)
        out[f"EyeSquint{side}"] = f(f"eyeSquint{side}", 0.0)
        out[f"BrowDown{side}"] = f(f"browDown{side}", 0.0)
        out[f"MouthSmile{side}"] = f(f"mouthSmile{side}", 0.0)
        out[f"MouthSad{side}"] = f(f"mouthFrown{side}", 0.0)
        out[f"SmileFrown{side}"] = smile(frame, side)
        out[f"MouthStretch{side}"] = f(f"mouthStretch{side}", 0.0)
        out[f"MouthPress{side}"] = f(f"mouthPress{side}", 0.0)
        out[f"MouthDimple{side}"] = f(f"mouthDimple{side}", 0.0)
    return out


class VrchatTrackingDriver:
    """VRChat 面捕驱动（满足面捕合成器的驱动契约）。"""

    name = "vrchat"

    def __init__(
        self,
        send: Callable[[str, Any], None],
        is_open: Callable[[], bool],
        *,
        face_prefix: Optional[str] = "FT/",
        markers: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Args:
        send: 发一条 OSC 消息（地址, 参数）。
        is_open: OSC 客户端是否在用。
        face_prefix: 面捕参数名前缀（``/avatar/parameters/<前缀>v2/<名字>``）；None = 不发脸部参数。
        markers: 风格化标记名 → 形象上的参数名。
        """
        self._send = send
        self._is_open = is_open
        self._face_prefix = face_prefix
        self._markers = dict(markers or {})
        # 每个形象参数最近一次发出去的值：只发变化了的
        self._sent: Dict[str, float] = {}

    def ready(self) -> bool:
        return self._is_open()

    def reset(self) -> None:
        """OSC 客户端重建或换形象后调用：忘掉已发的值，下一帧全部重发。"""
        self._sent.clear()

    def messages(self, frame: Frame) -> Tuple[Tuple[str, Any], ...]:
        """这一帧要发的 OSC 消息（地址, 参数）：眼动每帧都发，形象参数只发变化了的（不改状态）。"""
        closed = min(1.0, max(0.0, _avg(frame, "eyeBlink")))
        pitch = -frame.get(GAZE_Y, 0.0) * GAZE_PITCH_DEG
        yaw = -frame.get(GAZE_X, 0.0) * GAZE_YAW_DEG
        out = [(EYES_CLOSED, round(closed, 3)), (EYE_PITCH_YAW, [round(pitch + 0.0, 2), round(yaw + 0.0, 2)])]
        params: Dict[str, float] = {}
        if self._face_prefix is not None:
            for name, value in face_parameters(frame).items():
                params[f"{self._face_prefix}v2/{name}"] = value
        for marker, parameter in self._markers.items():
            params[parameter] = frame.get(MARKER_PREFIX + marker, 0.0)
        for parameter, value in params.items():
            value = round(value, 3)
            last = self._sent.get(parameter)
            if last is None or abs(value - last) >= SEND_EPSILON or (value in (0.0, 1.0) and value != last):
                out.append((f"/avatar/parameters/{parameter}", value))
        return tuple(out)

    async def apply(self, frame: Frame) -> None:
        """这一帧的消息逐条发出（UDP，发完就走）。"""
        for address, value in self.messages(frame):
            self._send(address, value)
            if address.startswith("/avatar/parameters/"):
                self._sent[address[len("/avatar/parameters/") :]] = value


__all__ = ["EYES_CLOSED", "EYE_PITCH_YAW", "GAZE_PITCH_DEG", "GAZE_YAW_DEG", "VrchatTrackingDriver", "face_parameters"]
