"""面捕合成器核心：持有身体状态，每次推进给出一整帧

纯逻辑、不碰事件和网络：时刻由调用方传入，随机源可注入（同种子同输入 = 同输出），
方便离线回放与测试。每帧按固定顺序合成，各层只管自己负责的部位：

人设静止姿态 → 心情底色 + 表情偏移 + 脉冲反应 + 思考神情 → 注意力（看哪里）与眼神 → 头部漫步 +
头部跟随视线 → 身体跟着头慢半拍转一点 → 表情系数逐个叠加（同侧的笑与撇相抵）→ 眨眼（乘在
表情的闭眼程度上，能叠在眯眼上）→ 说话时的嘴（下巴开合与元音口型归口型分析器，嘴角仍归
表情）→ 手动摆姿（调校时直接指定的通道值，盖过前面所有层）→ 钳制 → 限速 → 由视线换算眼球
朝向系数。

注意力按优先级决定看哪里：临时瞥一眼（弹幕、礼物）> 说话时看镜头 > 思考（刚来过
弹幕就先读弹幕，再把视线移开望向别处）> 空闲时看镜头。
"""

from __future__ import annotations

import math
import random
from typing import Dict, Mapping, Optional, Tuple

from .behaviors import BlinkGenerator, GazeGenerator, HeadWander
from .channels import (
    ARKIT_BLENDSHAPES,
    BODY_PITCH,
    BODY_ROLL,
    BODY_X,
    BODY_Y,
    BODY_YAW,
    BODY_Z,
    EYE_LOOK,
    GAZE_X,
    GAZE_Y,
    HEAD_PITCH,
    HEAD_ROLL,
    HEAD_YAW,
    MARKER_CHANNELS,
    VISEMES,
    Frame,
    clamp_frame,
    expand,
    eye_look_from_gaze,
    limit_slew,
    rest_frame,
)
from .config import TrackingConfig
from .expression import ExpressionScheduler, Recipe, build_recipes
from .mood import Mood
from .reactions import PulseLayer, SpeechProsody

# 思考时的神情：眉心微紧、眼睛略眯、嘴角稍收、抿一下嘴（按浮现权重叠加）
_THINKING_OFFSETS: Dict[str, float] = expand(
    {"browDown": 0.15, "eyeSquint": 0.1, "mouthSmile": -0.05, "mouthPress": 0.1}
)
# 除眼皮、眼球朝向外，表情系数都是"静止姿态 + 各层偏移"
_PLAIN_BLENDSHAPES = tuple(
    name for name in ARKIT_BLENDSHAPES if name not in EYE_LOOK and not name.startswith(("eyeBlink", "eyeWide"))
)
# 口型分析器的元音 → VRM 口型；说话的面孔在面捕里还会带出嘴唇形状（u、o 撮唇，i、e 咧开）
_VOWEL_VISEMES = dict(zip("AIUEO", VISEMES, strict=True))
_VOWEL_LIPS: Dict[str, Dict[str, float]] = {
    "U": expand({"mouthFunnel": 0.5, "mouthPucker": 0.3}),
    "O": expand({"mouthFunnel": 0.3}),
    "I": expand({"mouthStretch": 0.25}),
    "E": expand({"mouthStretch": 0.2}),
}


class TrackingSynthesizer:
    """面捕合成器核心（纯逻辑）。"""

    # 口型信号多久没更新就视为过期（分析器约 30~40 毫秒一帧）
    MOUTH_SIGNAL_FRESH_MS = 250
    # 头部跟随视线的迟滞（头比眼睛慢半拍）；跟着转多少度是调参
    HEAD_FOLLOW_TAU_MS = 350
    # 身体跟着头转的比例与迟滞（真人转头时肩背也带一点，比头更慢）
    BODY_FOLLOW = 0.3
    BODY_FOLLOW_TAU_MS = 600
    # 视线大幅跳动时顺带眨眼的概率
    GAZE_BLINK_CHANCE = 0.5
    # 思考神情的浮现与消退速度；思考时望向别处的落点隔几秒换一换
    THINKING_TAU_MS = 400
    AWAY_SHIFT_MS = (3000, 6000)
    # 思考时视线落点在配置的位置附近浮动多少
    AWAY_JITTER = 0.1

    def __init__(self, config: TrackingConfig, *, rng: Optional[random.Random] = None) -> None:
        self._config = config
        rng = rng or random.Random()
        self._rng = rng
        self._blink = BlinkGenerator(rng)
        self._gaze = GazeGenerator(rng)
        self._wander = HeadWander(rng)
        self._expressions = ExpressionScheduler(
            min_dwell_ms=config.expression_min_dwell_ms,
            intensity_floor=config.intensity_floor,
            intensity_gain=config.intensity_gain,
            recipes=build_recipes(config.recipes),
        )
        self._rest = rest_frame(config.rest_smile)
        self._head_wander_enabled = config.head_wander_enabled
        self._speaking = False
        self._mouth_open = 0.0
        self._vowels: Dict[str, float] = {}
        self._mouth_at_ms: Optional[int] = None
        self._follow_yaw = 0.0
        self._follow_pitch = 0.0
        self._body = [0.0, 0.0, 0.0]
        self._last: Optional[Frame] = None
        self._last_ms: Optional[int] = None
        self._pulses = PulseLayer()
        self._prosody = SpeechProsody(rng)
        # 注意力：思考中与否、先读弹幕到何时、望向别处的落点、临时瞥一眼的去处与截止
        self._thinking = False
        self._read_until: Optional[int] = None
        self._away_point: Tuple[float, float] = (0.0, 0.0)
        self._away_camera = True
        self._away_next: Optional[int] = None
        self._glance_point: Tuple[float, float] = (0.0, 0.0)
        self._glance_until: Optional[int] = None
        self._last_glance_ms = -(10**12)
        self._think_weight = 0.0
        self._mood = Mood(half_life_ms=config.mood_half_life_ms, gain=config.mood_gain)
        # 手动摆姿：调校时直接指定的通道值，到期自动失效（页面关了形象也不会卡在某个姿势）
        self._manual: Frame = {}
        self._manual_until: Optional[int] = None

    # ===== 调校 =====

    def reconfigure(self, config: TrackingConfig) -> None:
        """换一份调参并立刻生效（调校页拖滑条）：静止姿态、表情配方与调度、心情、头部微动。

        正在进行的表情、心情值、眼神与眨眼节奏都保留，只是之后按新参数算。
        """
        self._config = config
        self._rest = rest_frame(config.rest_smile)
        expressions = self._expressions
        expressions.min_dwell_ms = config.expression_min_dwell_ms
        expressions.intensity_floor = config.intensity_floor
        expressions.intensity_gain = config.intensity_gain
        expressions.set_recipes(build_recipes(config.recipes))
        self._mood.configure(half_life_ms=config.mood_half_life_ms, gain=config.mood_gain)
        self._head_wander_enabled = config.head_wander_enabled

    def recipes(self) -> Dict[str, Recipe]:
        """生效的表情配方（默认 + 人设覆盖）。"""
        return self._expressions.recipes

    def set_manual(self, values: Frame, until_ms: int) -> None:
        """手动指定若干通道的值并保持到 ``until_ms``（再次调用即续期；未知通道忽略）。"""
        self._manual = {name: float(value) for name, value in values.items() if name in self._rest}
        self._manual_until = until_ms

    def clear_manual(self) -> None:
        """结束手动摆姿，交还给自动行为（限速让形象平滑地回去）。"""
        self._manual = {}
        self._manual_until = None

    def manual_active(self, now_ms: int) -> bool:
        return self._manual_until is not None and now_ms < self._manual_until

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def last_frame(self) -> Optional[Frame]:
        """最近一次推进算出的帧（还没推进过时为 None）。"""
        return dict(self._last) if self._last is not None else None

    # ===== 输入 =====

    def set_speaking(self, speaking: bool) -> None:
        """正在播放语音与否（影响眨眼频率与嘴部归属）。"""
        self._speaking = speaking

    def on_mouth(self, mouth_open: float, now_ms: int, vowels: Optional[Mapping[str, float]] = None) -> None:
        """一帧口型信号（嘴张开量 0~1、元音成分 A/I/U/E/O）；同时从中找音节起点，触发韵律点头与挑眉。"""
        self._mouth_open = min(1.0, max(0.0, mouth_open))
        self._vowels = {k: min(1.0, max(0.0, v)) for k, v in (vowels or {}).items() if k in _VOWEL_VISEMES}
        self._mouth_at_ms = now_ms
        if self._config.speech_nods_enabled:
            for kind, scale in self._prosody.on_mouth(self._mouth_open, now_ms):
                self._pulses.trigger(kind, now_ms, scale)

    def on_mouth_end(self, now_ms: int) -> None:
        """一次发声的口型结束：嘴收回，偶尔收尾点一下头。"""
        self._mouth_open = 0.0
        self._vowels = {}
        self._mouth_at_ms = now_ms
        if self._config.speech_nods_enabled:
            for kind, scale in self._prosody.on_end(now_ms):
                self._pulses.trigger(kind, now_ms, scale)

    def begin_thinking(self, now_ms: int, *, read_first_ms: int = 0) -> None:
        """开始想下一句（已在想则不动）：``read_first_ms`` > 0 时先读弹幕这么久再移开视线。"""
        if self._thinking:
            return
        self._thinking = True
        self._read_until = now_ms + read_first_ms if read_first_ms > 0 else None
        self._away_next = None

    def end_thinking(self, now_ms: int) -> None:
        """想完了（开口或回到空闲）：视线回到镜头。"""
        self._thinking = False
        self._read_until = None

    @property
    def thinking(self) -> bool:
        return self._thinking

    def glance_at_chat(self, now_ms: int, *, duration_ms: int, min_gap_ms: int) -> bool:
        """瞥一眼弹幕区再回来；距上次瞥视不到 ``min_gap_ms`` 时不瞥（返回 False）。"""
        if now_ms - self._last_glance_ms < min_gap_ms:
            return False
        self._last_glance_ms = now_ms
        self.look_at((self._config.chat_gaze_x, self._config.chat_gaze_y), now_ms, duration_ms=duration_ms)
        return True

    def look_at(self, point: Tuple[float, float], now_ms: int, *, duration_ms: int) -> None:
        """看向某一点（-1~1）一会儿再回到原来的注意力（调校页拖眼神目标时预览用）。"""
        self._glance_point = point
        self._glance_until = now_ms + duration_ms

    def pulse(self, kind: str, now_ms: int, scale: float = 1.0) -> bool:
        """触发一个脉冲反应（点头、眼睛一亮、受惊一缩……；见 ``reactions.PULSES``），并轻推心情。"""
        triggered = self._pulses.trigger(kind, now_ms, scale)
        if triggered and self._config.mood_enabled:
            self._mood.push_pulse(kind, scale, now_ms)
        return triggered

    def mood(self, now_ms: int) -> tuple[float, float]:
        """此刻的心情（愉快度, 兴奋度），观察与日志用。"""
        self._mood.decay_to(now_ms)
        return round(self._mood.valence, 3), round(self._mood.arousal, 3)

    def express(
        self, emotion: str, intensity: float, now_ms: int, *, key: str, release_at_ms: Optional[int] = None
    ) -> str:
        """收到一个表情（转交调度器；返回 started / extended / deferred / released / unknown）。

        表情不论是马上上脸、延长还是排队，都算"有过这个情绪"，一并推动心情。
        """
        outcome = self._expressions.push(emotion, intensity, now_ms, key=key, release_at_ms=release_at_ms)
        if outcome in ("started", "extended", "deferred") and self._config.mood_enabled:
            self._mood.push_emotion(emotion, self._expressions.compress(intensity), now_ms)
        return outcome

    def release(self, key: str, at_ms: int) -> None:
        """把属于 ``key`` 的表情定在 ``at_ms`` 开始回落。"""
        self._expressions.release(key, at_ms)

    def set_head_wander(self, enabled: bool) -> None:
        self._head_wander_enabled = enabled

    @property
    def head_wander_enabled(self) -> bool:
        return self._head_wander_enabled

    def current_emotion(self, now_ms: int) -> str:
        return self._expressions.current_emotion(now_ms)

    def rest_frame(self) -> Frame:
        """人设静止姿态（驱动断开、程序退出前发这一帧，形象不会停在半闭眼之类的姿势上）。"""
        return dict(self._rest)

    # ===== 推进 =====

    def _attention(self, now_ms: int) -> Tuple[str, Tuple[float, float]]:
        """此刻看哪里：(眼神模式, 注视中心)。"""
        cfg = self._config
        if self._glance_until is not None:
            if now_ms < self._glance_until:
                return GazeGenerator.FIXATE, self._glance_point
            self._glance_until = None
        camera = (cfg.camera_gaze_x, cfg.camera_gaze_y)
        if self._speaking or not self._thinking or not cfg.thinking_gaze_enabled:
            return GazeGenerator.CAMERA, camera
        if self._read_until is not None and now_ms < self._read_until:
            return GazeGenerator.READING, (cfg.chat_gaze_x, cfg.chat_gaze_y)
        if self._away_next is None or now_ms >= self._away_next:
            rng = self._rng
            if self._away_next is not None and rng.random() < 0.2:
                # 想得久了偶尔看回镜头一下，再接着想
                self._away_camera = True
                self._away_next = now_ms + int(rng.uniform(900, 1600))
            else:
                # 思考时望向配置的落点（默认侧上方：真人思考时会避开视觉刺激）；隔几秒挪一挪，
                # 允许两侧都看时左右随机换，但大多留在同一侧
                if self._away_next is None or self._away_camera:
                    side = rng.choice((-1.0, 1.0))
                else:
                    side = 1.0 if self._away_point[0] * cfg.thinking_gaze_x >= 0 else -1.0
                if rng.random() < 0.25:
                    side = -side
                if not cfg.thinking_gaze_mirror:
                    side = 1.0
                jitter = self.AWAY_JITTER
                x = side * rng.uniform(cfg.thinking_gaze_x - jitter, cfg.thinking_gaze_x + jitter)
                y = rng.uniform(cfg.thinking_gaze_y - jitter, cfg.thinking_gaze_y + jitter)
                self._away_point = (x, y)
                self._away_camera = False
                self._away_next = now_ms + int(rng.uniform(*self.AWAY_SHIFT_MS))
        return GazeGenerator.FIXATE, camera if self._away_camera else self._away_point

    def step(self, now_ms: int) -> Frame:
        """推进到 ``now_ms`` 并返回这一帧。"""
        cfg = self._config
        dt_ms = 0 if self._last_ms is None else max(0, now_ms - self._last_ms)
        offsets, motion = self._expressions.sample(now_ms)
        for channel, value in self._pulses.sample(now_ms).items():
            offsets[channel] = offsets.get(channel, 0.0) + value
        if cfg.mood_enabled:
            # 心情底色叠在所有表情之下；兴奋时多动、平静疲惫时少动
            self._mood.decay_to(now_ms)
            for channel, value in self._mood.offsets().items():
                offsets[channel] = offsets.get(channel, 0.0) + value
            motion *= self._mood.motion_scale()
        target: Frame = dict(self._rest)

        # 注意力：决定眼神模式与注视中心；思考神情按浮现权重叠加，动作随之放慢
        mode, center = self._attention(now_ms)
        self._gaze.set_target(mode, center, now_ms)
        thinking_face = 1.0 if (mode == GazeGenerator.FIXATE and self._thinking and self._glance_until is None) else 0.0
        think_alpha = 1.0 - math.exp(-dt_ms / self.THINKING_TAU_MS) if dt_ms else 0.0
        self._think_weight += (thinking_face - self._think_weight) * think_alpha
        for channel, value in _THINKING_OFFSETS.items():
            offsets[channel] = offsets.get(channel, 0.0) + value * self._think_weight
        motion *= 1.0 - 0.3 * self._think_weight

        # 眼神：注视中心附近的微动 + 表情带来的视线偏移（害羞往下躲、尴尬往旁边看）
        if cfg.gaze_enabled:
            gaze_x, gaze_y, jumped = self._gaze.sample(now_ms)
        else:
            (gaze_x, gaze_y), jumped = center, False
        gaze_x += offsets.get(GAZE_X, 0.0)
        gaze_y += offsets.get(GAZE_Y, 0.0)
        target[GAZE_X] = gaze_x
        target[GAZE_Y] = gaze_y

        # 头部：漫步（幅度随表情收放）+ 跟随视线（一阶迟滞，头比眼慢）+ 表情带来的歪头低头
        if self._head_wander_enabled:
            yaw, pitch, roll = self._wander.sample(now_ms, cfg.head_wander_deg * motion)
        else:
            yaw = pitch = roll = 0.0
        alpha = 1.0 - math.exp(-dt_ms / self.HEAD_FOLLOW_TAU_MS) if dt_ms else 0.0
        self._follow_yaw += (gaze_x * cfg.head_follow_deg - self._follow_yaw) * alpha
        self._follow_pitch += (gaze_y * cfg.head_follow_deg * 0.6 - self._follow_pitch) * alpha
        target[HEAD_YAW] = yaw + self._follow_yaw + offsets.get(HEAD_YAW, 0.0)
        target[HEAD_PITCH] = pitch + self._follow_pitch + offsets.get(HEAD_PITCH, 0.0)
        target[HEAD_ROLL] = roll + offsets.get(HEAD_ROLL, 0.0)

        # 身体：跟着头慢半拍转一点，再加表情里的前倾、后缩
        body_alpha = 1.0 - math.exp(-dt_ms / self.BODY_FOLLOW_TAU_MS) if dt_ms else 0.0
        for index, (head, body) in enumerate(((HEAD_YAW, BODY_YAW), (HEAD_PITCH, BODY_PITCH), (HEAD_ROLL, BODY_ROLL))):
            self._body[index] += (target[head] * self.BODY_FOLLOW - self._body[index]) * body_alpha
            target[body] = self._body[index] + offsets.get(body, 0.0)
        for channel in (BODY_X, BODY_Y, BODY_Z):
            target[channel] = offsets.get(channel, 0.0)

        # 表情系数：静止姿态 + 各层偏移；同侧的笑与撇相抵只留净值（静止的浅笑碰上难过的撇嘴，
        # 结果是撇嘴，而不是笑和撇同时挂在脸上）
        for name in _PLAIN_BLENDSHAPES:
            target[name] = self._rest[name] + offsets.get(name, 0.0)
        for side in ("Left", "Right"):
            net = target[f"mouthSmile{side}"] - target[f"mouthFrown{side}"]
            target[f"mouthSmile{side}"], target[f"mouthFrown{side}"] = max(net, 0.0), max(-net, 0.0)
        # 风格化标记（脸红、眼泪……）只来自表情配方，静止时都没有
        for name in MARKER_CHANNELS:
            target[name] = offsets.get(name, 0.0)

        # 眼皮：表情决定闭多少（眯眼、半闭），眨眼在此基础上按比例再闭；睁大随眨眼收起
        if jumped and cfg.blink_enabled:
            self._blink.nudge(now_ms, self.GAZE_BLINK_CHANCE)
        if self._speaking:
            per_min = cfg.blink_per_min_speaking
        elif mode == GazeGenerator.READING:
            per_min = cfg.blink_per_min_reading
        else:
            per_min = cfg.blink_per_min_idle
        if cfg.mood_enabled:
            per_min *= self._mood.blink_scale()
        closure = self._blink.closure(now_ms, per_min=per_min) if cfg.blink_enabled else 0.0
        for side in ("Left", "Right"):
            shut = min(1.0, max(0.0, offsets.get(f"eyeBlink{side}", 0.0)))
            target[f"eyeBlink{side}"] = 1.0 - (1.0 - shut) * (1.0 - closure)
            target[f"eyeWide{side}"] = min(1.0, max(0.0, offsets.get(f"eyeWide{side}", 0.0))) * (1.0 - closure)

        # 嘴：说话期间下巴开合与元音口型只听口型分析器（信号过期就闭着），元音顺带出嘴唇形状；
        # 不说话时才轮到表情里的张嘴（惊讶）
        lip_fresh = self._mouth_at_ms is not None and now_ms - self._mouth_at_ms <= self.MOUTH_SIGNAL_FRESH_MS
        if self._speaking or (lip_fresh and self._mouth_open > 0.02):
            target["jawOpen"] = self._mouth_open if lip_fresh else 0.0
            vowels = self._vowels if lip_fresh and self._mouth_open > 0.02 else {}
            for vowel, viseme in _VOWEL_VISEMES.items():
                weight = vowels.get(vowel, 0.0)
                target[viseme] = weight
                for name, value in _VOWEL_LIPS.get(vowel, {}).items():
                    target[name] += value * weight

        # 手动摆姿盖过所有自动层（到期即作废，之后按限速平滑回到自动）
        if self._manual_until is not None:
            if now_ms < self._manual_until:
                target.update(self._manual)
            else:
                self.clear_manual()

        target = clamp_frame(target)
        frame = target if self._last is None else limit_slew(self._last, target, dt_ms)
        frame.update(eye_look_from_gaze(frame[GAZE_X], frame[GAZE_Y]))
        self._last = frame
        self._last_ms = now_ms
        return frame


__all__ = ["TrackingSynthesizer"]
