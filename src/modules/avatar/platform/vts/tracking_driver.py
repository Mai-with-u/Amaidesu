"""VTS 面捕驱动：把面捕合成器的一帧换算成 VTS 捕捉输入，每帧一次注入、发完就走

VTS 把注入的捕捉输入按模型作者自己调好的映射曲线换成模型参数，效果等于"被一个摄像头
面捕着"。换算照 VBridger 的做法：VBridger 把 iPhone 面捕的 ARKit 系数按公式算成一组
VTS 输入，其中 VTS 自带的（头部角度、睁眼、眼球、眉、嘴角、张嘴等）直接写，VTS 没有的
（下巴、撮唇、撅嘴到咧嘴、抿唇到露牙、眉心、下眼睑、身体角度与位置）作为自建输入，名字
和范围已成为 Live2D 约稿里"支持 VBridger"的通行规范。面捕合成器给出的正是 ARKit 系数，
这里就是一个内置的 VBridger：按规范把两组都写全，模型接了哪些就动哪些。

同一个输入值在不同模型上的含义不一样（实测桃濑日和的"眼睛开合"输入 1.0 已是瞪到
最大、正常睁眼约 0.53），所以"正常睁眼""中性嘴角""中性眉"对应的输入值是校准项，
从配置传入，换模型时按观感调（形象调校页可以实时调）。

风格化标记（脸红、眼泪……）按配置登记的对应关系写：登记成 VTS 表情文件（``.exp3.json``）的，
值越过阈值时整体淡入、落回时淡出（带回差，在阈值附近抖动不会反复开关）；登记成输入名的，
每帧按 0~1 照写，在 VTS 里接到模型作者做的参数上。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Set, Tuple

from src.modules.avatar.tracking.channels import (
    BODY_PITCH,
    BODY_ROLL,
    BODY_X,
    BODY_Y,
    BODY_YAW,
    BODY_Z,
    GAZE_X,
    GAZE_Y,
    HEAD_PITCH,
    HEAD_ROLL,
    HEAD_YAW,
    MARKER_PREFIX,
    MARKERS,
    Frame,
    brow_height,
    eye_openness,
    smile,
)
from src.modules.utils.time_utils import now_ms as wall_now_ms

# 调试台手动写参数时，覆盖保持多久（之后交还面捕合成器）
OVERRIDE_HOLD_MS = 3000

# VBridger 规范里 VTS 没有自带、要作为自建输入存在的那组：名字 → (最小, 默认, 最大, 说明)。
# 名字与范围照 VBridger 在 VTS 里建的原样（装过 VBridger 的 VTS 里已有，没有就由适配器补建）
VBRIDGER_INPUTS: Dict[str, Tuple[float, float, float, str]] = {
    "JawOpen": (0.0, 0.0, 1.0, "下巴张开"),
    "MouthFunnel": (0.0, 0.0, 1.0, "嘴唇撮成 O 型"),
    "MouthPucker": (-1.0, 0.0, 1.0, "撅嘴（-1）到咧开（+1）"),
    "MouthPressLipOpen": (-1.3, 0.0, 1.3, "抿唇（负）到露牙（正）"),
    "MouthShrug": (0.0, 0.0, 1.0, "嘴唇往上嘟"),
    "BrowInnerUp": (0.0, 0.0, 1.0, "眉心上提"),
    "EyeSquintL": (0.0, 0.0, 1.0, "左下眼睑上推（笑眼）"),
    "EyeSquintR": (0.0, 0.0, 1.0, "右下眼睑上推（笑眼）"),
    "BodyAngleX": (-50.0, 0.0, 50.0, "身体左右转"),
    "BodyAngleY": (-50.0, 0.0, 50.0, "身体前后倾"),
    "BodyAngleZ": (-50.0, 0.0, 50.0, "身体左右歪"),
    "BodyPositionX": (-15.0, 0.0, 15.0, "身体左右位置"),
    "BodyPositionY": (-5.0, 0.0, 5.0, "身体上下位置"),
    "BodyPositionZ": (-10.0, 0.0, 10.0, "身体前后位置"),
    "VBridgerFaceFound": (0.0, 0.0, 1.0, "是否检测到脸"),
}

# 风格化标记登记成这个后缀的是 VTS 表情文件，其余按输入名写
EXPRESSION_FILE_SUFFIX = ".exp3.json"
# 表情文件型标记：值到这么大才打开、落到这么小才关上（回差防抖）；VTS 自带淡入淡出的秒数
MARKER_ON = 0.35
MARKER_OFF = 0.2
MARKER_FADE_S = 0.3

# 身体位置 ±1 对应的 VTS 输入量：取各输入范围的一半，前倾后仰都留有余量
_FACE_POSITION_SCALE = (7.5, 7.5, 5.0)
_BODY_POSITION_SCALE = (7.5, 2.5, 5.0)


def _centered(value: float, neutral: float) -> float:
    """以 0 为中性的 -1~1 → 以 ``neutral`` 为中性的 0~1 输入（两侧分别线性拉伸）。"""
    if value >= 0:
        return neutral + value * (1.0 - neutral)
    return neutral + value * neutral


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


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
        markers: Optional[Mapping[str, str]] = None,
        clock: Callable[[], int] = wall_now_ms,
    ) -> None:
        """Args:
        client: VTS 客户端（需有 ``is_open`` / ``authenticated`` 与异步 ``send``）。
        eye_open_rest: 模型上"正常睁眼"对应的 EyeOpen 输入值。
        mouth_smile_neutral: 中性嘴角对应的 MouthSmile 输入值。
        brow_neutral: 中性眉对应的眉高输入值。
        markers: 风格化标记名 → 本模型上的表情文件名（``.exp3.json``）或 VTS 输入名。
        """
        self._client = client
        self._eye_open_rest = eye_open_rest
        self._mouth_smile_neutral = mouth_smile_neutral
        self._brow_neutral = brow_neutral
        self._clock = clock
        self._ranges: Dict[str, Tuple[float, float]] = {}
        self._overrides: Dict[str, Tuple[float, int]] = {}
        self._active = False
        markers = dict(markers or {})
        self._marker_files = {m: t for m, t in markers.items() if t.endswith(EXPRESSION_FILE_SUFFIX)}
        self._marker_inputs = {m: t for m, t in markers.items() if not t.endswith(EXPRESSION_FILE_SUFFIX)}
        # 表情文件此刻开着没有（不在表里 = 不知道，下一帧按目标状态发一次对齐）
        self._file_on: Dict[str, bool] = {}
        # 当前模型有哪些表情文件（None = 不知道，按都有处理）
        self._available_files: Optional[Set[str]] = None

    # ===== 状态 =====

    def set_active(self, active: bool) -> None:
        """适配器在连接授权成功后打开、断开时关闭（重连后表情文件的开关状态重新对齐）。"""
        self._active = active
        self._file_on.clear()

    def set_available_expressions(self, files: Optional[Iterable[str]]) -> None:
        """当前模型的表情文件清单（连接与换模型时刷新）；None = 查不到，按都有处理。"""
        self._available_files = set(files) if files is not None else None
        self._file_on.clear()

    def marker_inputs(self) -> Dict[str, str]:
        """登记成 VTS 输入的风格化标记：输入名 → 说明（适配器据此补建 VTS 里没有的）。"""
        return {name: f"风格化标记：{MARKERS[marker]}" for marker, name in self._marker_inputs.items()}

    def missing_marker_files(self) -> Dict[str, str]:
        """登记了但当前模型上没有的表情文件：标记名 → 文件名（查不到清单时为空）。"""
        if self._available_files is None:
            return {}
        return {m: f for m, f in self._marker_files.items() if f not in self._available_files}

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
        """一帧规范通道 → VTS 输入值（未钳制）：先是 VTS 自带的，再是 VBridger 的那组自建输入。"""
        f = frame.get
        rest = self._eye_open_rest

        def eye(side: str) -> float:
            # 正常睁开对应校准值，睁大从校准值往 1 拉；眨眼与眯眼已经算进了闭眼系数
            return eye_openness(frame, side) * rest + f(f"eyeWide{side}", 0.0) * (1.0 - rest)

        def both(name: str) -> float:
            return (f(f"{name}Left", 0.0) + f(f"{name}Right", 0.0)) / 2.0

        brow_left = _centered(brow_height(frame, "Left"), self._brow_neutral)
        brow_right = _centered(brow_height(frame, "Right"), self._brow_neutral)
        # VTS 的眼球左右输入与头部左右转输入方向相反（实测两者同为正时，模型头转向一边、
        # 眼球转向另一边），左右取反后"看向某处"时头和眼才朝同一边；上下两者同向，不用取反
        gaze_x, gaze_y = -f(GAZE_X, 0.0), f(GAZE_Y, 0.0)
        jaw = f("jawOpen", 0.0)
        body_x, body_y, body_z = f(BODY_X, 0.0), f(BODY_Y, 0.0), f(BODY_Z, 0.0)
        return {
            "FaceAngleX": f(HEAD_YAW, 0.0),
            "FaceAngleY": f(HEAD_PITCH, 0.0),
            "FaceAngleZ": f(HEAD_ROLL, 0.0),
            "FacePositionX": body_x * _FACE_POSITION_SCALE[0],
            "FacePositionY": body_y * _FACE_POSITION_SCALE[1],
            "FacePositionZ": body_z * _FACE_POSITION_SCALE[2],
            "EyeOpenLeft": eye("Left"),
            "EyeOpenRight": eye("Right"),
            "EyeLeftX": gaze_x,
            "EyeLeftY": gaze_y,
            "EyeRightX": gaze_x,
            "EyeRightY": gaze_y,
            "BrowLeftY": brow_left,
            "BrowRightY": brow_right,
            "Brows": (brow_left + brow_right) / 2.0,
            "MouthSmile": _centered(smile(frame), self._mouth_smile_neutral),
            # 嘴唇分开的程度：下巴张开但嘴唇合着（mouthClose）时不算张嘴
            "MouthOpen": max(0.0, jaw - f("mouthClose", 0.0)),
            "MouthX": f("mouthLeft", 0.0) - f("mouthRight", 0.0),
            "CheekPuff": f("cheekPuff", 0.0),
            "TongueOut": f("tongueOut", 0.0),
            "FaceAngry": _clamp(both("browDown") * 0.7 + both("noseSneer") * 0.6, 0.0, 1.0),
            "VoiceA": f("aa", 0.0),
            "VoiceI": f("ih", 0.0),
            "VoiceU": f("ou", 0.0),
            "VoiceE": f("ee", 0.0),
            "VoiceO": f("oh", 0.0),
            # 以下是 VBridger 的自建输入
            "JawOpen": jaw,
            "MouthFunnel": f("mouthFunnel", 0.0),
            # 撅嘴与咧开是同一根轴的两头：酒窝与咧嘴往外拉，撅嘴往里收
            "MouthPucker": _clamp(both("mouthDimple") + both("mouthStretch") - f("mouthPucker", 0.0), -1.0, 1.0),
            # 抿唇与露牙也是同一根轴：上唇上提、下唇下拉露出牙，抿嘴、卷唇把嘴唇收进去
            "MouthPressLipOpen": _clamp(
                both("mouthUpperUp")
                + both("mouthLowerDown")
                - both("mouthPress")
                - (f("mouthRollUpper", 0.0) + f("mouthRollLower", 0.0)) / 2.0,
                -1.3,
                1.3,
            ),
            "MouthShrug": (f("mouthShrugUpper", 0.0) + f("mouthShrugLower", 0.0)) / 2.0,
            "BrowInnerUp": f("browInnerUp", 0.0),
            "EyeSquintL": f("eyeSquintLeft", 0.0),
            "EyeSquintR": f("eyeSquintRight", 0.0),
            "BodyAngleX": f(BODY_YAW, 0.0),
            "BodyAngleY": f(BODY_PITCH, 0.0),
            "BodyAngleZ": f(BODY_ROLL, 0.0),
            "BodyPositionX": body_x * _BODY_POSITION_SCALE[0],
            "BodyPositionY": body_y * _BODY_POSITION_SCALE[1],
            "BodyPositionZ": body_z * _BODY_POSITION_SCALE[2],
            "VBridgerFaceFound": 1.0,
            # 登记成输入的风格化标记照写 0~1
            **{name: f(MARKER_PREFIX + marker, 0.0) for marker, name in self._marker_inputs.items()},
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

    def marker_file_changes(self, frame: Frame) -> Dict[str, bool]:
        """这一帧要切换开关的表情文件：文件名 → 开 / 关（只列和当前状态不同的，不改状态）。"""
        changes: Dict[str, bool] = {}
        for marker, file in self._marker_files.items():
            if self._available_files is not None and file not in self._available_files:
                continue
            value = frame.get(MARKER_PREFIX + marker, 0.0)
            on = self._file_on.get(file)
            want = value >= (MARKER_OFF if on else MARKER_ON)
            if on is None or want != on:
                changes[file] = want
        return changes

    async def apply(self, frame: Frame) -> None:
        """这一帧换算后整组注入一次，表情文件型标记只在开关变化时发一次（都是发完就走）。"""
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
        for file, on in self.marker_file_changes(frame).items():
            await self._client.send(
                "ExpressionActivationRequest", {"expressionFile": file, "fadeTime": MARKER_FADE_S, "active": on}
            )
            self._file_on[file] = on


__all__ = [
    "EXPRESSION_FILE_SUFFIX",
    "MARKER_FADE_S",
    "MARKER_OFF",
    "MARKER_ON",
    "OVERRIDE_HOLD_MS",
    "VBRIDGER_INPUTS",
    "VtsTrackingDriver",
]
