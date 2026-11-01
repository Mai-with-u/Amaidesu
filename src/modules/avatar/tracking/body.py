"""身体动作层：什么时候换站姿、什么时候叠一个身体动作、什么时候做一个手势

面捕数据只管脸、头和躯干的前后左右；手臂和整体的身体语言要靠成套的动作（Warudo 自带的
人形动画、VTS 上画师做的动作文件、VRChat 的动作菜单）。业界没有像 ARKit 那样通行的身体
动作词表，这里定一小套平台无关的动作名，平台驱动各自翻译成本平台的做法，做不到的跳过。

三类动作：

- **站姿**（stance）：换上后一直保持，直到换下一个。跟着"持续的情绪"走——主表情保持一会儿
  才换，换了至少停一段时间，情绪回到平静后过一会儿才回普通站姿；Planner 想得久了摆思考站姿，
  开口时立刻放下。站姿全身都动，比表情显眼得多，所以比表情稳得多；
- **叠加动作**（overlay）：说话时在短语开头偶尔叠一下（左右摇身体、开心时仰头笑），做完自己
  消失，彼此隔开足够久，不会变成打拍子；
- **手势**（gesture）：一次性的动作（挥手、指一下……），由直播间、游戏事件或主播点名触发，
  同一个手势有最短间隔（主播点名的不受限）。

调度器只决定"该做什么"，产出动作提示交给运行时转发；纯逻辑、不碰时钟，测试注入时刻即可。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Collection, Dict, List, Mapping, Optional

from .expression import RECIPES

if TYPE_CHECKING:  # 循环 import 规避：调参模块要从这里取站姿词表做校验
    from .config import TrackingConfig

STANCE = "stance"
OVERLAY = "overlay"
GESTURE = "gesture"

# 站姿：名字 → 说明（说明给人看，平台翻译时按名字找自己的做法）
STANCES: Dict[str, str] = {
    "standing": "普通站姿",
    "hands_front": "双手放在身前",
    "fist_up": "右手举拳",
    "lean_in": "身体前倾",
    "hand_on_hip": "单手叉腰",
    "hands_clasped": "双手交握在身前",
    "scratch_head": "右手挠后脑勺",
    "worried": "右手抬在身前（担心）",
    "crying": "哭",
    "hands_on_hips": "双手叉腰",
    "refuse": "拒绝",
    "hands_open": "双手摊开在身前",
    "head_in_hands": "双手抱头",
    "puzzled": "疑惑（“啥？”）",
    "hands_behind": "双手背到身后",
    "facepalm": "手捂脸",
    "slouch": "垂肩驼背",
    "thinking": "思考",
}
OVERLAYS: Dict[str, str] = {"sway": "左右摇一下身体", "laugh": "仰头笑"}
BODY_GESTURES: Dict[str, str] = {"wave": "挥手", "wave_both": "双手挥", "point": "指一下", "cheer": "举拳庆祝"}
KINDS: Dict[str, Dict[str, str]] = {STANCE: STANCES, OVERLAY: OVERLAYS, GESTURE: BODY_GESTURES}

# 主播点名用的中文名 → 手势名（和点头、摇头这些有意的动作放在同一个目录里）
GESTURE_ACTIONS: Dict[str, str] = {desc: name for name, desc in BODY_GESTURES.items()}

# "在想下一步"这个状态在情绪 → 站姿对照里的键
THINKING = "thinking"
NEUTRAL = "neutral"

# 情绪（和"在想"）→ 站姿的默认对照
DEFAULT_STANCES: Dict[str, str] = {
    "neutral": "standing",
    "happy": "hands_front",
    "excited": "fist_up",
    "love": "lean_in",
    "smug": "hand_on_hip",
    "shy": "hands_clasped",
    "embarrassed": "scratch_head",
    "sad": "worried",
    "crying": "crying",
    "angry": "hands_on_hips",
    "disgusted": "refuse",
    "surprised": "hands_open",
    "scared": "head_in_hands",
    "confused": "puzzled",
    "serious": "hands_behind",
    "speechless": "facepalm",
    "tired": "slouch",
    THINKING: "thinking",
}

# 开心类情绪说话时才会仰头笑
_LAUGHING = frozenset({"happy", "excited"})
# 口型信号里找短语开头：嘴张到这么大算开口，低于这么小算闭嘴，停顿这么久以上算新短语
_ONSET_LEVEL = 0.22
_SILENCE_LEVEL = 0.06
_PHRASE_GAP_MS = 300
# 两个叠加动作之间至少隔这么久（不论哪一种）
_OVERLAY_SPACING_MS = 4000


@dataclass(frozen=True)
class BodyCue:
    """一个身体动作提示：类别（站姿 / 叠加动作 / 手势）+ 动作名。"""

    kind: str
    name: str


def kind_of(name: str) -> str:
    """身体动作名属于哪一类（三类的名字互不重名）；不认识的抛 ValueError（带合法名单）。"""
    for kind, names in KINDS.items():
        if name in names:
            return kind
    known = ", ".join(n for names in KINDS.values() for n in names)
    raise ValueError(f"未知的身体动作 {name!r}（可用：{known}）")


def check_action_map(
    value: Mapping[str, str], *, what: str, kinds: Collection[str] = (STANCE, OVERLAY, GESTURE)
) -> Dict[str, str]:
    """平台配置里"身体动作名 → 本平台的做法"的校验：名字要认识、属于允许的类别，做法不能为空。

    返回去掉首尾空白的副本。``what`` 是做法的叫法（动画、热键……），写进报错。
    """
    cleaned: Dict[str, str] = {}
    for name, target in value.items():
        if kind_of(name) not in kinds:
            raise ValueError(f"身体动作 {name} 在这个平台上不能登记（只能登记：{'、'.join(kinds)}）")
        if not str(target).strip():
            raise ValueError(f"身体动作 {name} 没写对应的{what}")
        cleaned[name] = str(target).strip()
    return cleaned


def check_stance_map(value: Mapping[str, str]) -> None:
    """情绪 → 站姿对照的校验：键是情绪名（以表情配方为准）或 thinking，值是站姿词表里的名字。"""
    keys = set(RECIPES) | {NEUTRAL, THINKING}
    for key, stance in value.items():
        if key not in keys:
            raise ValueError(f"站姿对照里的 {key!r} 不是情绪名，可用：{sorted(keys)}")
        if stance not in STANCES:
            raise ValueError(f"{key} 对应的站姿 {stance!r} 不在词表里，可用：{sorted(STANCES)}")


class BodyDirector:
    """身体动作调度：每帧看情绪、思考、说话状态决定站姿，口型找短语开头叠动作，事件转手势。"""

    def __init__(self, config: "TrackingConfig") -> None:
        self._config = config
        self.stance = DEFAULT_STANCES[NEUTRAL]
        self._stance_since: Optional[int] = None
        self._thought_pose = False
        self._emotion = NEUTRAL
        self._emotion_since: Optional[int] = None
        self._thinking_since: Optional[int] = None
        self._last: Dict[str, int] = {}
        self._last_overlay: Optional[int] = None
        self._mouth_open = False
        self._quiet_since: Optional[int] = None
        self._cues: List[BodyCue] = []

    def configure(self, config: "TrackingConfig") -> None:
        self._config = config

    def take_cues(self) -> List[BodyCue]:
        """取走攒下的动作提示（运行时每帧取一次转给平台驱动）。"""
        cues, self._cues = self._cues, []
        return cues

    def rest_cue(self) -> BodyCue:
        """回到普通站姿的提示（驱动断开、程序退出前发，形象不会停在哭、抱头这类站姿上）。"""
        return BodyCue(STANCE, self._stances().get(NEUTRAL, DEFAULT_STANCES[NEUTRAL]))

    def _stances(self) -> Mapping[str, str]:
        return self._config.body_stances

    # ===== 站姿 =====

    def step(self, now_ms: int, *, emotion: str, thinking: bool, speaking: bool) -> None:
        """推进到 ``now_ms``：该换站姿就攒一个站姿提示。"""
        if self._emotion_since is None or emotion != self._emotion:
            self._emotion, self._emotion_since = emotion, now_ms
        if thinking and not speaking:
            if self._thinking_since is None:
                self._thinking_since = now_ms
        else:
            self._thinking_since = None
        if not self._config.body_enabled:
            return
        target, urgent = self._target(now_ms)
        if target == self.stance:
            # 想完开口时这句话的站姿恰好就是思考站姿：不用换，但从此按普通规则走
            self._thought_pose = self._thought_pose and not urgent
            return
        if target is None:
            return
        held = self._stance_since is None or now_ms - self._stance_since >= self._config.body_stance_hold_ms
        if not (held or urgent):
            return
        self.stance, self._stance_since = target, now_ms
        self._thought_pose = self._thinking_since is not None and target == self._stances().get(THINKING)
        self._cues.append(BodyCue(STANCE, target))

    def _target(self, now_ms: int) -> tuple[Optional[str], bool]:
        """此刻想换到的站姿（None = 不换）与是否立刻换（不受最短停留约束）。"""
        cfg, stances = self._config, self._stances()
        emotion = self._emotion
        since = now_ms if self._emotion_since is None else self._emotion_since
        if self._thinking_since is not None and now_ms - self._thinking_since >= cfg.body_think_after_ms:
            return stances.get(THINKING), False
        if self._thought_pose:
            # 想完开口：放下思考站姿，直接换到这句话情绪的站姿（情绪平静就回普通站姿）
            return stances.get(emotion) or stances.get(NEUTRAL), True
        if emotion == NEUTRAL:
            return (stances.get(NEUTRAL) if now_ms - since >= cfg.body_stance_return_ms else None), False
        return (stances.get(emotion) if now_ms - since >= cfg.body_stance_settle_ms else None), False

    # ===== 叠加动作 =====

    def on_mouth(self, mouth_open: float, now_ms: int) -> None:
        """一帧口型信号：在短语开头（停顿后重新开口）偶尔叠一个身体动作。"""
        if mouth_open < _SILENCE_LEVEL:
            if self._mouth_open or self._quiet_since is None:
                self._quiet_since = now_ms
            self._mouth_open = False
            return
        if self._mouth_open or mouth_open < _ONSET_LEVEL:
            return
        self._mouth_open = True
        if self._quiet_since is not None and now_ms - self._quiet_since < _PHRASE_GAP_MS:
            return
        self._phrase(now_ms)

    def on_mouth_end(self, now_ms: int) -> None:
        """一次发声结束：下一句的第一个开口算短语开头。"""
        self._mouth_open = False
        self._quiet_since = None

    def _phrase(self, now_ms: int) -> None:
        cfg = self._config
        if not cfg.body_enabled:
            return
        if self._last_overlay is not None and now_ms - self._last_overlay < _OVERLAY_SPACING_MS:
            return
        if self._emotion in _LAUGHING and self._due("laugh", now_ms, cfg.body_laugh_gap_ms):
            self._overlay("laugh", now_ms)
        elif self._due("sway", now_ms, cfg.body_sway_gap_ms):
            self._overlay("sway", now_ms)

    def _due(self, name: str, now_ms: int, gap_ms: int) -> bool:
        last = self._last.get(name)
        return last is None or now_ms - last >= gap_ms

    def _overlay(self, name: str, now_ms: int) -> None:
        self._last[name] = self._last_overlay = now_ms
        self._cues.append(BodyCue(OVERLAY, name))

    # ===== 手势 =====

    def gesture(self, name: str, now_ms: int, *, force: bool = False) -> bool:
        """做一个手势；同一手势距上次不到最短间隔时不做（``force`` = 主播点名，不受限）。"""
        if name not in BODY_GESTURES or not self._config.body_enabled:
            return False
        if not force and not self._due(name, now_ms, self._config.body_gesture_gap_ms):
            return False
        self._last[name] = now_ms
        self._cues.append(BodyCue(GESTURE, name))
        return True


__all__ = [
    "BODY_GESTURES",
    "DEFAULT_STANCES",
    "GESTURE",
    "GESTURE_ACTIONS",
    "KINDS",
    "OVERLAY",
    "OVERLAYS",
    "STANCE",
    "STANCES",
    "THINKING",
    "BodyCue",
    "BodyDirector",
    "check_action_map",
    "check_stance_map",
    "kind_of",
]
