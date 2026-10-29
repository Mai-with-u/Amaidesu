"""VTS 面捕驱动：把面捕合成器的一帧换算成 VTS 捕捉输入，每帧一次注入、发完就走

VTS 把注入的捕捉输入（FaceAngleX、EyeOpenLeft、MouthSmile……）按模型作者自己调好的
映射曲线换成模型参数，效果等于"被一个摄像头面捕着"。这里只做两件事：通用通道 →
VTS 输入的换算，以及按 VTS 报告的输入范围钳制。

同一个输入值在不同模型上的含义不一样（实测桃濑日和的"眼睛开合"输入 1.0 已是瞪到
最大、正常睁眼约 0.53），所以"正常睁眼""中性嘴角""中性眉"对应的输入值是校准项，
从配置传入，换模型时按观感调（形象调校页可以实时调）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from src.modules.avatar.tracking.channels import (
    ANGRY,
    BROW_L,
    BROW_R,
    CHEEK_PUFF,
    EYE_OPEN_L,
    EYE_OPEN_R,
    EYE_WIDE,
    GAZE_X,
    GAZE_Y,
    HEAD_PITCH,
    HEAD_ROLL,
    HEAD_YAW,
    MOUTH_OPEN,
    MOUTH_SMILE,
    MOUTH_X,
    Frame,
)
from src.modules.utils.time_utils import now_ms as wall_now_ms

# 调试台手动写参数时，覆盖保持多久（之后交还面捕合成器）
OVERRIDE_HOLD_MS = 3000


def _centered(value: float, neutral: float) -> float:
    """以 0 为中性的 -1~1 → 以 ``neutral`` 为中性的 0~1 输入（两侧分别线性拉伸）。"""
    if value >= 0:
        return neutral + value * (1.0 - neutral)
    return neutral + value * neutral


class VtsTrackingDriver:
    """VTS 面捕驱动（满足面捕合成器的驱动契约）。"""

    name = "vts"

    def __init__(
        self,
        client: Any,
        *,
        eye_open_rest: float,
        mouth_smile_neutral: float,
        brow_neutral: float,
        clock: Callable[[], int] = wall_now_ms,
    ) -> None:
        """Args:
        client: VTS 客户端（需有 ``is_open`` / ``authenticated`` 与异步 ``send``）。
        eye_open_rest: 模型上"正常睁眼"对应的 EyeOpen 输入值。
        mouth_smile_neutral: 中性嘴角对应的 MouthSmile 输入值。
        brow_neutral: 中性眉对应的眉高输入值。
        """
        self._client = client
        self._eye_open_rest = eye_open_rest
        self._mouth_smile_neutral = mouth_smile_neutral
        self._brow_neutral = brow_neutral
        self._clock = clock
        self._ranges: Dict[str, Tuple[float, float]] = {}
        self._overrides: Dict[str, Tuple[float, int]] = {}
        self._active = False

    # ===== 状态 =====

    def set_active(self, active: bool) -> None:
        """适配器在连接授权成功后打开、断开时关闭。"""
        self._active = active

    def set_input_ranges(self, ranges: Dict[str, Tuple[float, float]]) -> None:
        """VTS 报告的各输入范围（连接与换模型时刷新）；空 = 不钳制、全部输入都发。"""
        self._ranges = dict(ranges)

    def ready(self) -> bool:
        return self._active and bool(getattr(self._client, "is_open", False))

    def set_override(self, name: str, value: float, *, hold_ms: int = OVERRIDE_HOLD_MS) -> None:
        """调试台手动写某个 VTS 输入：在接下来 ``hold_ms`` 内盖过面捕合成器的值。"""
        self._overrides[name] = (float(value), self._clock() + hold_ms)

    # ===== 换算 =====

    def calibration(self) -> Dict[str, float]:
        """换算校准项当前值（键与 VTS 配置段里的同名字段对应，调校页保存时原样写回）。"""
        return {
            "eye_open_rest": self._eye_open_rest,
            "mouth_smile_neutral": self._mouth_smile_neutral,
            "brow_neutral": self._brow_neutral,
        }

    def calibrate(self, values: Mapping[str, float]) -> None:
        """改校准项，下一帧起按新值换算；不认识的键整批拒绝（抛 ValueError，一项都不改）。"""
        unknown = sorted(set(values) - set(self.calibration()))
        if unknown:
            raise ValueError(f"未知校准项 {unknown}（合法名：{', '.join(self.calibration())}）")
        self._eye_open_rest = float(values.get("eye_open_rest", self._eye_open_rest))
        self._mouth_smile_neutral = float(values.get("mouth_smile_neutral", self._mouth_smile_neutral))
        self._brow_neutral = float(values.get("brow_neutral", self._brow_neutral))

    def to_inputs(self, frame: Frame) -> Dict[str, float]:
        """一帧通用通道 → VTS 输入值（未钳制）。"""
        rest = self._eye_open_rest

        def eye(open_channel: str) -> float:
            # 正常睁开对应校准值，睁大从校准值往 1 拉；眨眼与眯眼已经乘进了开合量
            return frame.get(open_channel, 1.0) * rest + frame.get(EYE_WIDE, 0.0) * (1.0 - rest)

        brow_left = _centered(frame.get(BROW_L, 0.0), self._brow_neutral)
        brow_right = _centered(frame.get(BROW_R, 0.0), self._brow_neutral)
        # VTS 的眼球左右输入与头部左右转输入方向相反（实测两者同为正时，模型头转向一边、
        # 眼球转向另一边），左右取反后"看向某处"时头和眼才朝同一边；上下两者同向，不用取反
        gaze_x, gaze_y = -frame.get(GAZE_X, 0.0), frame.get(GAZE_Y, 0.0)
        return {
            "FaceAngleX": frame.get(HEAD_YAW, 0.0),
            "FaceAngleY": frame.get(HEAD_PITCH, 0.0),
            "FaceAngleZ": frame.get(HEAD_ROLL, 0.0),
            "EyeOpenLeft": eye(EYE_OPEN_L),
            "EyeOpenRight": eye(EYE_OPEN_R),
            "EyeLeftX": gaze_x,
            "EyeLeftY": gaze_y,
            "EyeRightX": gaze_x,
            "EyeRightY": gaze_y,
            "BrowLeftY": brow_left,
            "BrowRightY": brow_right,
            "Brows": (brow_left + brow_right) / 2.0,
            "MouthSmile": _centered(frame.get(MOUTH_SMILE, 0.0), self._mouth_smile_neutral),
            "MouthOpen": frame.get(MOUTH_OPEN, 0.0),
            "MouthX": frame.get(MOUTH_X, 0.0),
            "CheekPuff": frame.get(CHEEK_PUFF, 0.0),
            "FaceAngry": frame.get(ANGRY, 0.0),
        }

    def _finalize(self, inputs: Dict[str, float]) -> Dict[str, float]:
        now = self._clock()
        for name, (value, until) in list(self._overrides.items()):
            if now >= until:
                del self._overrides[name]
            else:
                inputs[name] = value
        if not self._ranges:
            return inputs
        # 只发 VTS 认识的输入，并钳到它报告的范围内
        out: Dict[str, float] = {}
        for name, value in inputs.items():
            bounds: Optional[Tuple[float, float]] = self._ranges.get(name)
            if bounds is not None:
                out[name] = min(bounds[1], max(bounds[0], value))
        return out

    async def apply(self, frame: Frame) -> None:
        """这一帧换算后整组注入一次（发完就走）。"""
        inputs = self._finalize(self.to_inputs(frame))
        await self._client.send(
            "InjectParameterDataRequest",
            {
                # 注入时必须声明"脸部已捕捉"，否则 VTS 会周期性触发"失去捕捉"把模型重置
                "faceFound": True,
                "mode": "set",
                "parameterValues": [{"id": name, "value": round(value, 4)} for name, value in inputs.items()],
            },
        )


__all__ = ["OVERRIDE_HOLD_MS", "VtsTrackingDriver"]
