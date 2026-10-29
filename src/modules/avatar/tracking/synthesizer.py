"""面捕合成器核心：持有身体状态，每次推进给出一整帧

纯逻辑、不碰事件和网络：时刻由调用方传入，随机源可注入（同种子同输入 = 同输出），
方便离线回放与测试。每帧按固定顺序合成，各层只管自己负责的部位：

人设静止姿态 → 表情偏移 + 脉冲反应 + 思考神情 → 注意力（看哪里）与眼神 → 头部漫步 +
头部跟随视线 → 眨眼（对眼皮做乘法，能叠在眯眼上）→ 口型（说话时下巴开合归口型，嘴角
的笑与撇仍归表情）→ 钳制 → 限速。

注意力按优先级决定看哪里：临时瞥一眼（弹幕、礼物）> 说话时看镜头 > 思考（刚来过
弹幕就先读弹幕，再把视线移开望向别处）> 空闲时看镜头。
"""

from __future__ import annotations

import math
import random
from typing import Dict, Optional, Tuple

from .behaviors import BlinkGenerator, GazeGenerator, HeadWander
from .channels import (
    ANGRY,
    BROW,
    BROW_L,
    BROW_R,
    CHEEK_PUFF,
    EYE_OPEN,
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
    clamp_frame,
    limit_slew,
    rest_frame,
)
from .config import TrackingConfig
from .expression import ExpressionScheduler
from .reactions import PulseLayer, SpeechProsody

# 思考时的神情：眉心微紧、眼睛略眯、嘴角稍收（按浮现权重叠加）
_THINKING_OFFSETS: Dict[str, float] = {BROW: -0.15, EYE_OPEN: -0.06, MOUTH_SMILE: -0.05}


class TrackingSynthesizer:
    """面捕合成器核心（纯逻辑）。"""

    # 口型信号多久没更新就视为过期（分析器约 30~40 毫秒一帧）
    MOUTH_SIGNAL_FRESH_MS = 250
    # 视线偏到边上时头部跟着转的角度，以及跟随的迟滞（头比眼睛慢半拍）
    HEAD_FOLLOW_DEG = 8.0
    HEAD_FOLLOW_TAU_MS = 350
    # 视线大幅跳动时顺带眨眼的概率
    GAZE_BLINK_CHANCE = 0.5
    # 思考神情的浮现与消退速度；思考时望向别处的落点隔几秒换一换
    THINKING_TAU_MS = 400
    AWAY_SHIFT_MS = (3000, 6000)

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
        )
        self._rest = rest_frame(config.rest_smile)
        self._head_wander_enabled = config.head_wander_enabled
        self._speaking = False
        self._mouth_open = 0.0
        self._mouth_at_ms: Optional[int] = None
        self._follow_yaw = 0.0
        self._follow_pitch = 0.0
        self._last: Optional[Frame] = None
        self._last_ms: Optional[int] = None
        self._pulses = PulseLayer()
        self._prosody = SpeechProsody(rng)
        # 注意力：思考中与否、先读弹幕到何时、望向别处的落点、临时瞥一眼的去处与截止
        self._thinking = False
        self._read_until: Optional[int] = None
        self._away_point: Tuple[float, float] = (0.0, 0.0)
        self._away_next: Optional[int] = None
        self._glance_point: Tuple[float, float] = (0.0, 0.0)
        self._glance_until: Optional[int] = None
        self._last_glance_ms = -(10**12)
        self._think_weight = 0.0

    # ===== 输入 =====

    def set_speaking(self, speaking: bool) -> None:
        """正在播放语音与否（影响眨眼频率与嘴部归属）。"""
        self._speaking = speaking

    def on_mouth(self, mouth_open: float, now_ms: int) -> None:
        """一帧口型信号（嘴张开量 0~1）；同时从中找音节起点，触发韵律点头与挑眉。"""
        self._mouth_open = min(1.0, max(0.0, mouth_open))
        self._mouth_at_ms = now_ms
        if self._config.speech_nods_enabled:
            for kind, scale in self._prosody.on_mouth(self._mouth_open, now_ms):
                self._pulses.trigger(kind, now_ms, scale)

    def on_mouth_end(self, now_ms: int) -> None:
        """一次发声的口型结束：嘴收回，偶尔收尾点一下头。"""
        self._mouth_open = 0.0
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
        self._glance_point = (self._config.chat_gaze_x, self._config.chat_gaze_y)
        self._glance_until = now_ms + duration_ms
        return True

    def pulse(self, kind: str, now_ms: int, scale: float = 1.0) -> bool:
        """触发一个脉冲反应（点头、眼睛一亮、受惊一缩……；见 ``reactions.PULSES``）。"""
        return self._pulses.trigger(kind, now_ms, scale)

    def express(
        self, emotion: str, intensity: float, now_ms: int, *, key: str, release_at_ms: Optional[int] = None
    ) -> str:
        """收到一个表情（转交调度器；返回 started / extended / deferred / released / unknown）。"""
        return self._expressions.push(emotion, intensity, now_ms, key=key, release_at_ms=release_at_ms)

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
        if self._speaking or not self._thinking or not cfg.thinking_gaze_enabled:
            return GazeGenerator.CAMERA, (0.0, 0.0)
        if self._read_until is not None and now_ms < self._read_until:
            return GazeGenerator.READING, (cfg.chat_gaze_x, cfg.chat_gaze_y)
        if self._away_next is None or now_ms >= self._away_next:
            # 思考时望向侧上方（真人思考时会避开视觉刺激）；隔几秒挪一挪，大多留在同一侧
            rng = self._rng
            side = (
                (1.0 if self._away_point[0] >= 0 else -1.0) if self._away_next is not None else rng.choice((-1.0, 1.0))
            )
            if rng.random() < 0.25:
                side = -side
            self._away_point = (side * rng.uniform(0.3, 0.5), rng.uniform(0.15, 0.35))
            self._away_next = now_ms + int(rng.uniform(*self.AWAY_SHIFT_MS))
        return GazeGenerator.FIXATE, self._away_point

    def step(self, now_ms: int) -> Frame:
        """推进到 ``now_ms`` 并返回这一帧。"""
        cfg = self._config
        dt_ms = 0 if self._last_ms is None else max(0, now_ms - self._last_ms)
        offsets, motion = self._expressions.sample(now_ms)
        for channel, value in self._pulses.sample(now_ms).items():
            offsets[channel] = offsets.get(channel, 0.0) + value
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
        self._follow_yaw += (gaze_x * self.HEAD_FOLLOW_DEG - self._follow_yaw) * alpha
        self._follow_pitch += (gaze_y * self.HEAD_FOLLOW_DEG * 0.6 - self._follow_pitch) * alpha
        target[HEAD_YAW] = yaw + self._follow_yaw + offsets.get(HEAD_YAW, 0.0)
        target[HEAD_PITCH] = pitch + self._follow_pitch + offsets.get(HEAD_PITCH, 0.0)
        target[HEAD_ROLL] = roll + offsets.get(HEAD_ROLL, 0.0)

        # 眼皮：表情决定睁多开（眯眼、半闭），眨眼在此基础上按比例闭合
        if jumped and cfg.blink_enabled:
            self._blink.nudge(now_ms, self.GAZE_BLINK_CHANCE)
        if self._speaking:
            per_min = cfg.blink_per_min_speaking
        elif mode == GazeGenerator.READING:
            per_min = cfg.blink_per_min_reading
        else:
            per_min = cfg.blink_per_min_idle
        closure = self._blink.closure(now_ms, per_min=per_min) if cfg.blink_enabled else 0.0
        both = offsets.get(EYE_OPEN, 0.0)
        for channel in (EYE_OPEN_L, EYE_OPEN_R):
            openness = min(1.0, max(0.0, 1.0 + both + offsets.get(channel, 0.0)))
            target[channel] = openness * (1.0 - closure)
        target[EYE_WIDE] = min(1.0, max(0.0, offsets.get(EYE_WIDE, 0.0))) * (1.0 - closure)

        brow = offsets.get(BROW, 0.0)
        target[BROW_L] = brow + offsets.get(BROW_L, 0.0)
        target[BROW_R] = brow + offsets.get(BROW_R, 0.0)
        target[MOUTH_SMILE] = self._rest[MOUTH_SMILE] + offsets.get(MOUTH_SMILE, 0.0)
        for channel in (MOUTH_X, CHEEK_PUFF, ANGRY):
            target[channel] = offsets.get(channel, 0.0)

        # 嘴：说话期间下巴开合只听口型（信号过期就闭着）；不说话时才轮到表情里的张嘴（惊讶）
        lip_fresh = self._mouth_at_ms is not None and now_ms - self._mouth_at_ms <= self.MOUTH_SIGNAL_FRESH_MS
        if self._speaking or (lip_fresh and self._mouth_open > 0.02):
            target[MOUTH_OPEN] = self._mouth_open if lip_fresh else 0.0
        else:
            target[MOUTH_OPEN] = offsets.get(MOUTH_OPEN, 0.0)

        target = clamp_frame(target)
        frame = target if self._last is None else limit_slew(self._last, target, dt_ms)
        self._last = frame
        self._last_ms = now_ms
        return frame


__all__ = ["TrackingSynthesizer"]
