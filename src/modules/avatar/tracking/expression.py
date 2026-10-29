"""表情配方与调度：情绪标签 → 带时间曲线的通道偏移

情绪标签（17 个枚举值）继续是 LLM 的词汇，这里把每个标签展开成一份"表演配方"：
满强度时各通道偏移多少（脸、眼神、头部一起），以及头部微动幅度要不要收一收。

调度规则（解决"表情切得太频繁"）：
- 每个表情有起、持、落：按族决定浮现快慢（惊吓 0.1 秒，社交姿态 0.4 秒），持续到
  它所属的那句话结束再留一会儿，最后缓慢落回人设静止姿态；
- 新表情和当前表情同族、强度差不到 0.3：只延长当前表情，不重来；
- 换成别的表情前，当前表情至少要停留够最短时长，不够就排队等着；
- 惊、怕这类强情绪（强度 ≥ 0.6）可以打断；
- 给定强度先压缩：说话时的表情大多是淡的，满幅留给真正的高潮。

默认配方是通用的；按人设改某个情绪（比如开心时眯成 ^^ 眼）走配置里的配方覆盖，
由 :func:`build_recipes` 合成生效配方，调校页改完可以不重启直接换上。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

from .behaviors import smootherstep
from .channels import (
    ARKIT_BLENDSHAPES,
    BILATERAL,
    BODY_Z,
    CHANNEL_LIMITS,
    EYE_LOOK,
    GAZE_X,
    GAZE_Y,
    HEAD_PITCH,
    HEAD_ROLL,
    HEAD_YAW,
    POSE_CHANNELS,
    expand,
)

NEUTRAL = "neutral"

# 表情族：决定浮现与回落的快慢，也决定"同族只延长"的判定
POSITIVE = "positive"
NEGATIVE = "negative"
STARTLE = "startle"
COGNITIVE = "cognitive"
SOCIAL = "social"
STATE = "state"

# 族 → (浮现毫秒, 回落毫秒)
FAMILY_TIMING: Dict[str, Tuple[int, int]] = {
    POSITIVE: (300, 1500),
    NEGATIVE: (300, 1500),
    STARTLE: (100, 1000),
    COGNITIVE: (300, 1200),
    SOCIAL: (400, 1500),
    STATE: (600, 2500),
}

# 被新表情顶替时，旧表情用这么短的回落让位，两者交叉淡入淡出
CROSSFADE_RELEASE_MS = 400
# 能打断最短停留的强情绪：惊吓族、给定强度不低于此值
BREAKTHROUGH_INTENSITY = 0.6
# 同族表情强度差小于此值时只延长当前表情
EXTEND_INTENSITY_DELTA = 0.3


@dataclass(frozen=True)
class Recipe:
    """一个情绪的表演配方：满强度时的通道偏移 + 头部微动幅度倍数。"""

    family: str
    offsets: Mapping[str, float]
    motion: float = 1.0


# 偏移是"满强度"的量；实际叠加量 = 偏移 × 压缩后的强度 × 当前时间曲线值。键用 ARKit 表情系数名
# （去掉 Left / Right 的是双侧简写），加上头部、身体、视线这些姿态通道。真笑带动脸颊和下眼睑
# （cheekSquint、eyeSquint），难过时眉心上提（browInnerUp），这些都照面部动作的常识写。
RECIPES: Dict[str, Recipe] = {
    "happy": Recipe(
        POSITIVE, {"mouthSmile": 0.8, "cheekSquint": 0.4, "eyeSquint": 0.35, "browOuterUp": 0.2, HEAD_PITCH: 2.0}
    ),
    "excited": Recipe(
        POSITIVE,
        {
            "mouthSmile": 1.0,
            "cheekSquint": 0.4,
            "eyeWide": 0.5,
            "browInnerUp": 0.4,
            "browOuterUp": 0.6,
            "jawOpen": 0.2,
            HEAD_PITCH: 3.0,
            BODY_Z: 0.2,
        },
        1.3,
    ),
    "love": Recipe(
        POSITIVE,
        {"mouthSmile": 0.8, "eyeSquint": 0.4, "eyeBlink": 0.2, "cheekSquint": 0.3, "browInnerUp": 0.2, HEAD_ROLL: 5.0},
    ),
    "sad": Recipe(
        NEGATIVE,
        {"mouthFrown": 0.6, "browInnerUp": 0.6, "eyeBlink": 0.3, "mouthPress": 0.2, HEAD_PITCH: -4.0, GAZE_Y: -0.3},
        0.7,
    ),
    "crying": Recipe(
        NEGATIVE,
        {
            "mouthFrown": 0.8,
            "browInnerUp": 0.8,
            "eyeBlink": 0.5,
            "eyeSquint": 0.4,
            "jawOpen": 0.3,
            "mouthStretch": 0.3,
            HEAD_PITCH: -6.0,
        },
    ),
    "angry": Recipe(
        NEGATIVE,
        {
            "browDown": 0.8,
            "noseSneer": 0.4,
            "mouthFrown": 0.4,
            "mouthPress": 0.4,
            "eyeSquint": 0.2,
            HEAD_PITCH: -2.0,
        },
    ),
    "disgusted": Recipe(
        NEGATIVE,
        {
            "noseSneer": 0.7,
            "mouthUpperUp": 0.4,
            "mouthFrown": 0.3,
            "mouthRight": 0.4,
            "browDown": 0.5,
            "eyeSquint": 0.4,
            HEAD_YAW: -6.0,
        },
    ),
    "surprised": Recipe(
        STARTLE,
        {"browInnerUp": 1.0, "browOuterUp": 1.0, "eyeWide": 1.0, "jawOpen": 0.5, HEAD_PITCH: 4.0, BODY_Z: -0.1},
    ),
    "scared": Recipe(
        STARTLE,
        {
            "browInnerUp": 0.9,
            "browOuterUp": 0.5,
            "eyeWide": 0.8,
            "mouthStretch": 0.5,
            "jawOpen": 0.3,
            HEAD_PITCH: -3.0,
            BODY_Z: -0.2,
        },
    ),
    "confused": Recipe(
        COGNITIVE,
        {"browOuterUpLeft": 0.6, "browDownRight": 0.3, "mouthLeft": 0.3, "mouthPucker": 0.2, HEAD_ROLL: 8.0},
        0.6,
    ),
    "serious": Recipe(COGNITIVE, {"mouthPress": 0.3, "browDown": 0.3, "mouthFrown": 0.1, "eyeSquint": 0.15}, 0.6),
    "shy": Recipe(
        SOCIAL,
        {
            "mouthSmile": 0.35,
            "mouthPress": 0.2,
            "eyeBlink": 0.3,
            GAZE_X: 0.4,
            GAZE_Y: -0.6,
            HEAD_PITCH: -5.0,
            HEAD_ROLL: 4.0,
            BODY_Z: -0.1,
        },
        0.7,
    ),
    "embarrassed": Recipe(
        SOCIAL,
        {
            "mouthSmile": 0.2,
            "mouthStretch": 0.2,
            "mouthLeft": 0.3,
            "browInnerUp": 0.3,
            "eyeBlink": 0.25,
            GAZE_X: -0.5,
            GAZE_Y: -0.3,
        },
    ),
    "smug": Recipe(
        SOCIAL,
        {
            "mouthSmileLeft": 0.6,
            "mouthSmileRight": 0.15,
            "mouthLeft": 0.3,
            "eyeSquint": 0.4,
            "browOuterUpLeft": 0.5,
            HEAD_PITCH: 5.0,
            HEAD_ROLL: -3.0,
        },
        0.6,
    ),
    "speechless": Recipe(SOCIAL, {"mouthPress": 0.3, "mouthFrown": 0.1, "eyeBlink": 0.35, "browDown": 0.1}, 0.3),
    "tired": Recipe(
        STATE,
        {"eyeBlink": 0.45, "eyeBlinkLeft": 0.05, "browDown": 0.2, "jawOpen": 0.08, HEAD_PITCH: -4.0, BODY_Z: -0.1},
        0.6,
    ),
}

# 配方里能写的键：姿态通道、ARKit 表情系数（眼球朝向由视线换算，不在其中）与双侧简写；
# 口型只在说话时由口型分析器给出，表情不写。覆盖里另可写头部微动幅度倍数
RECIPE_KEYS: Tuple[str, ...] = (
    *POSE_CHANNELS,
    *(name for name in ARKIT_BLENDSHAPES if name not in EYE_LOOK),
    *BILATERAL,
)
MOTION_KEY = "motion"
MOTION_RANGE: Tuple[float, float] = (0.0, 3.0)


def recipe_key_span(key: str) -> float:
    """配方里某个键允许的最大偏移量（该通道取值范围的跨度；简写按左侧通道算）。"""
    channel = BILATERAL[key][0] if key in BILATERAL else key
    low, high = CHANNEL_LIMITS[channel]
    return high - low


def check_recipe_overrides(overrides: Mapping[str, Mapping[str, float]]) -> None:
    """校验配方覆盖：情绪名、键名、取值都要认识，否则抛 ValueError（带合法名单）。"""
    for emotion, values in overrides.items():
        if emotion not in RECIPES:
            raise ValueError(f"未知情绪 '{emotion}'（合法名：{', '.join(RECIPES)}）")
        for key, value in values.items():
            if key == MOTION_KEY:
                if not MOTION_RANGE[0] <= value <= MOTION_RANGE[1]:
                    raise ValueError(f"{emotion}.{key} = {value} 超出 {MOTION_RANGE[0]}~{MOTION_RANGE[1]}")
                continue
            if key not in RECIPE_KEYS:
                raise ValueError(f"{emotion} 里有未知通道 '{key}'（合法名：{', '.join(RECIPE_KEYS)}、{MOTION_KEY}）")
            span = recipe_key_span(key)
            if abs(value) > span:
                raise ValueError(f"{emotion}.{key} = {value} 超出 ±{span:g}")


def build_recipes(overrides: Mapping[str, Mapping[str, float]]) -> Dict[str, Recipe]:
    """默认配方 + 人设覆盖 → 生效配方。

    写了覆盖的情绪整份替换通道偏移（族与浮现快慢不变，免得改个嘴角连调度都变了）；
    写了 motion 就换掉头部微动倍数，没写沿用默认。
    """
    recipes = dict(RECIPES)
    for emotion, values in overrides.items():
        base = RECIPES[emotion]
        offsets = {key: float(value) for key, value in values.items() if key != MOTION_KEY}
        recipes[emotion] = Recipe(base.family, offsets, float(values.get(MOTION_KEY, base.motion)))
    return recipes


@dataclass
class _Layer:
    """一个正在生效的表情：强度、起始时刻与时间曲线参数。"""

    emotion: str
    recipe: Recipe
    level: float
    key: str
    start_ms: int
    attack_ms: int
    release_ms: int
    release_at_ms: Optional[int] = None

    def _attack(self, now_ms: int) -> float:
        return smootherstep((now_ms - self.start_ms) / max(self.attack_ms, 1))

    def envelope(self, now_ms: int) -> float:
        """当前时间曲线值（0~1）：浮现 → 保持 → 从开始回落那一刻的值缓慢落到 0。"""
        if self.release_at_ms is not None and now_ms >= self.release_at_ms:
            from_value = self._attack(self.release_at_ms)
            return from_value * (1.0 - smootherstep((now_ms - self.release_at_ms) / max(self.release_ms, 1)))
        return self._attack(now_ms)

    def releasing(self, now_ms: int) -> bool:
        return self.release_at_ms is not None and now_ms >= self.release_at_ms

    def finished(self, now_ms: int) -> bool:
        return self.release_at_ms is not None and now_ms >= self.release_at_ms + self.release_ms


@dataclass
class _Pending:
    """排队等最短停留时间过去的表情。"""

    emotion: str
    level: float
    key: str
    release_at_ms: Optional[int]


class ExpressionScheduler:
    """表情调度：决定哪个表情此刻在脸上、各占多少。"""

    MAX_LAYERS = 3

    def __init__(
        self,
        *,
        min_dwell_ms: int,
        intensity_floor: float,
        intensity_gain: float,
        recipes: Optional[Mapping[str, Recipe]] = None,
    ) -> None:
        self.min_dwell_ms = min_dwell_ms
        self.intensity_floor = intensity_floor
        self.intensity_gain = intensity_gain
        self._recipes: Dict[str, Recipe] = dict(RECIPES if recipes is None else recipes)
        self._layers: List[_Layer] = []
        self._pending: Optional[_Pending] = None

    @property
    def recipes(self) -> Dict[str, Recipe]:
        """生效配方（默认 + 人设覆盖）的副本。"""
        return dict(self._recipes)

    def set_recipes(self, recipes: Mapping[str, Recipe]) -> None:
        """换一套配方；正在脸上的表情也立刻按新配方算（调校时拖滑条马上看到效果）。"""
        self._recipes = dict(recipes)
        for layer in self._layers:
            layer.recipe = self._recipes.get(layer.emotion, layer.recipe)

    def compress(self, intensity: float) -> float:
        """给定强度 → 实际幅度：下限 + 系数 × 强度，封顶 1。"""
        return min(1.0, self.intensity_floor + self.intensity_gain * min(1.0, max(0.0, intensity)))

    def _top(self, now_ms: int) -> Optional[_Layer]:
        """最近开始、还没进入回落的表情（"当前表情"）。"""
        for layer in reversed(self._layers):
            if not layer.releasing(now_ms):
                return layer
        return None

    def _start(self, emotion: str, level: float, key: str, release_at_ms: Optional[int], now_ms: int) -> None:
        # 顶替：旧表情立刻用短回落让位，新表情从现在开始浮现，两者交叉淡化
        top = self._top(now_ms)
        if top is not None:
            top.release_at_ms = now_ms
            top.release_ms = min(top.release_ms, CROSSFADE_RELEASE_MS)
        recipe = self._recipes[emotion]
        attack_ms, release_ms = FAMILY_TIMING[recipe.family]
        self._layers.append(_Layer(emotion, recipe, level, key, now_ms, attack_ms, release_ms, release_at_ms))
        del self._layers[: max(0, len(self._layers) - self.MAX_LAYERS)]

    def push(
        self, emotion: str, intensity: float, now_ms: int, *, key: str, release_at_ms: Optional[int] = None
    ) -> str:
        """收到一个表情。返回处理结果：started / extended / deferred / released / unknown。

        ``key`` 标识它属于哪一句话（或哪次工具调用），之后 :meth:`release` 按它收尾；
        ``release_at_ms`` 是预定的回落时刻（None = 等 :meth:`release`）。
        """
        if emotion == NEUTRAL:
            # 中性不是一张新脸：让当前表情开始回落，排队中的也作废
            top = self._top(now_ms)
            if top is not None:
                top.release_at_ms = now_ms
            self._pending = None
            return "released"
        recipe = self._recipes.get(emotion)
        if recipe is None:
            return "unknown"
        level = self.compress(intensity)
        top = self._top(now_ms)
        if top is not None and (top.emotion == emotion or top.recipe.family == recipe.family):
            if abs(level - top.level) < EXTEND_INTENSITY_DELTA:
                # 同族、强度相近：脸上的表情不动，只把它的归属和回落时刻换成新的这一句
                top.key = key
                top.release_at_ms = release_at_ms
                return "extended"
        breakthrough = recipe.family == STARTLE and intensity >= BREAKTHROUGH_INTENSITY
        if top is not None and not breakthrough and now_ms - top.start_ms < self.min_dwell_ms:
            # 当前表情停留还不够久：新表情排队（只留最新的一个），够了再换上
            self._pending = _Pending(emotion, level, key, release_at_ms)
            return "deferred"
        self._pending = None
        self._start(emotion, level, key, release_at_ms, now_ms)
        return "started"

    def release(self, key: str, at_ms: int) -> None:
        """把属于 ``key`` 的表情的回落时刻定为 ``at_ms``（句子播完 + 停留，或播放失败）。"""
        for layer in self._layers:
            if layer.key == key:
                layer.release_at_ms = at_ms
        if self._pending is not None and self._pending.key == key:
            self._pending.release_at_ms = at_ms

    def sample(self, now_ms: int) -> Tuple[Dict[str, float], float]:
        """当前各通道偏移之和，以及头部微动幅度倍数。"""
        pending = self._pending
        if pending is not None:
            if pending.release_at_ms is not None and pending.release_at_ms <= now_ms:
                # 排队期间它那句话已经说完：时机过了，不再补演
                self._pending = None
            else:
                top = self._top(now_ms)
                if top is None or now_ms - top.start_ms >= self.min_dwell_ms:
                    self._pending = None
                    self._start(pending.emotion, pending.level, pending.key, pending.release_at_ms, now_ms)
        self._layers = [layer for layer in self._layers if not layer.finished(now_ms)]
        offsets: Dict[str, float] = {}
        motion = 1.0
        for layer in self._layers:
            weight = layer.envelope(now_ms) * layer.level
            for channel, value in expand(layer.recipe.offsets).items():
                offsets[channel] = offsets.get(channel, 0.0) + weight * value
            motion += weight * (layer.recipe.motion - 1.0)
        return offsets, max(0.0, motion)

    def current_emotion(self, now_ms: int) -> str:
        """此刻脸上的主表情（观察与日志用；没有表情时为 neutral）。"""
        top = self._top(now_ms)
        return top.emotion if top is not None else NEUTRAL


__all__ = [
    "BREAKTHROUGH_INTENSITY",
    "FAMILY_TIMING",
    "MOTION_KEY",
    "MOTION_RANGE",
    "NEUTRAL",
    "RECIPES",
    "RECIPE_KEYS",
    "ExpressionScheduler",
    "Recipe",
    "build_recipes",
    "check_recipe_overrides",
    "recipe_key_span",
]
