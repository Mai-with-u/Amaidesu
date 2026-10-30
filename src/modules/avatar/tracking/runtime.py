"""面捕合成器运行时：订阅事实事件喂给合成器，按帧率推进，把每一帧发给挂上的平台驱动

时序（表情跟着声音走，不再比声音早一个合成时长）：
- ``streamer.speech``：开着 TTS 时先记下这句话的情绪，不上脸；没开 TTS 时立刻上脸，
  按字数估算这句话说多久；
- ``tts.utterance.started``：这句话真正开始播放，表情从此刻浮现，同时进入"说话中"；
- ``tts.utterance.finished``：播完，表情再停留一会儿后回落；
- ``tts.utterance.failed``：没播出来，记下的情绪作废，已上脸的立刻回落。

非说话时间（都是现成的事实事件，主播包不为形象多发任何东西）：
- ``streamer.stage``：Planner 开始想（planning）就进入思考——前几秒来过弹幕就先读弹幕，
  再把视线移开；回到空闲（idle）思考结束；
- ``room.message.*``：弹幕到达时瞥一眼弹幕区（正在想或正在说时不瞥，避免左顾右盼）；
  礼物、SC、上舰再眼睛一亮；
- ``game.*``：重大进展眼睛一亮、需要关注"诶？"一下、出错皱眉一缩——比主播开口说这件事
  早好几秒，正好是人"先一惊、再开口"的顺序。

主播有意发起的动作（点头、眨一只眼……）多半和一句话同一步发出，而那句话要先合成几秒语音
才开口；动作若立刻做就抢了拍。所以主播在想的时候发来的动作先记下，``planner.decision``
说这一轮要说话，就绑到那句话上、等它开始播放再做；这一轮不说话、那句没播出来、或等太久，
就立刻做。

口型分析器的信号经一个只记数值的渲染器接进来，帧循环每帧取最新值。没有驱动挂着
时帧循环不跑，零开销。所有路径 fail-soft：驱动写失败、事件处理异常只记日志；合成器推进
出错就换一台新的从静止姿态重来，帧循环某一拍出错只跳过那一拍。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import OrderedDict, deque
from typing import (
    Any,
    Awaitable,
    Callable,
    Deque,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Set,
    Tuple,
    runtime_checkable,
)

from src.modules.avatar.lipsync import LipSyncRenderer, MouthSignal
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.planner import PlannerDecisionPayload, StreamerStagePayload
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms as wall_now_ms

from .channels import Frame
from .config import TrackingConfig
from .expression import Recipe
from .gestures import GESTURES, HOLD_EXPRESSION, LOOK_AT_CHAT, LOOK_AT_CHAT_MS
from .synthesizer import TrackingSynthesizer


@runtime_checkable
class TrackingDriver(Protocol):
    """平台驱动契约：把通用通道的一帧写成本平台的输入。

    ``apply`` 要尽快返回（发出去就走，不等平台回复），否则会拖慢帧循环；
    ``ready`` 为假时（未连接、未校准）这一帧跳过它。
    """

    name: str

    def ready(self) -> bool: ...

    async def apply(self, frame: Frame) -> None: ...


@runtime_checkable
class CalibratableDriver(Protocol):
    """带换算校准项的驱动（比如 VTS 上"正常睁眼"对应哪个输入值）：调校页实时调。

    校准项的键与该平台配置段里的同名字段一一对应，保存时原样写回。
    """

    name: str

    def calibration(self) -> Dict[str, float]: ...

    def calibrate(self, values: Mapping[str, float]) -> None: ...


class _MouthTap(LipSyncRenderer):
    """接在口型分析器上的"渲染器"：只把嘴张开量与元音成分交给合成器，不写任何平台参数。

    合成器出错后会被换成一台新的，所以每次现取当前那台，不在构造时记死。
    """

    def __init__(self, synthesizer: Callable[[], TrackingSynthesizer], clock: Callable[[], int]) -> None:
        self._synthesizer = synthesizer
        self._clock = clock

    async def on_mouth_signal(self, signal: MouthSignal) -> None:
        self._synthesizer().on_mouth(signal.mouth_open, self._clock(), signal.vowels)

    async def on_session_end(self) -> None:
        self._synthesizer().on_mouth_end(self._clock())


class TrackingRuntime:
    """面捕合成器运行时（组合根持有，所有平台驱动共用一个）。"""

    # 记下但还没开始播放的发言情绪最多留多少条、留多久（播放失败没有回音的兜底）
    PENDING_LIMIT = 32
    # 帧率与卡顿统计看最近多少帧（30 帧/秒下约 10 秒）
    TICK_HISTORY = 300
    # 跟着下一句做的动作最多等多久（语音合成常见几秒，冷启动更久），过了就直接做
    DEFERRED_ACT_MAX_WAIT_MS = 20_000
    PENDING_TTL_MS = 120_000
    # 收到"开始播放"后先按预计时长 + 余量排一个回落时刻，"播完"事件到了再以它为准；
    # 万一播完事件丢了，表情也不会一直停在脸上
    SAFETY_MARGIN_MS = 2000
    # 开始思考前这么久之内来过弹幕，就先读弹幕再想
    READ_WINDOW_MS = 8000
    # 瞥一眼弹幕区：弹幕看得短、两次至少隔 5 秒；礼物类看得久一点、间隔也短
    DANMAKU_GLANCE = (900, 5000)
    PAID_GLANCE = (1200, 1500)
    # 付费消息眼睛一亮的幅度；游戏事件 → 脉冲（名字, 幅度）
    PAID_BRIGHTEN: Dict[str, float] = {"gift": 0.6, "super_chat": 0.9, "guard": 1.0}
    GAME_PULSES: Dict[str, Tuple[str, float]] = {
        "milestone": ("brighten", 0.8),
        "attention_required": ("alert", 1.0),
        "error": ("wince", 0.8),
    }

    def __init__(
        self,
        config: TrackingConfig,
        *,
        event_bus: Optional[EventBus] = None,
        lipsync_analyzer: Optional[Any] = None,
        tts_enabled: bool = True,
        clock: Callable[[], int] = wall_now_ms,
        rng: Optional[random.Random] = None,
        run_loop: bool = True,
    ) -> None:
        """Args:
        tts_enabled: 是否装配了 TTS（决定表情等"开始播放"还是发言即上脸）。
        clock: 毫秒时刻来源（离线回放与测试注入假时钟）。
        rng: 随机源（同种子可复现同一串眨眼、眼神与漫步）。
        run_loop: False = 不起帧循环，由调用方逐帧调 :meth:`tick_once`（离线回放与测试）。
        """
        self.logger = get_logger("TrackingRuntime")
        self._config = config
        self._run_loop_enabled = run_loop
        self._event_bus = event_bus
        self._lipsync_analyzer = lipsync_analyzer
        self._tts_enabled = tts_enabled
        self._clock = clock
        self._rng = rng
        self.synthesizer = TrackingSynthesizer(config, rng=rng)
        self._mouth_tap = _MouthTap(lambda: self.synthesizer, clock)
        self._drivers: List[TrackingDriver] = []
        self._driver_errors: dict[str, int] = {}
        # 合成器推进一帧出错的累计次数（每次出错都换一台新的，见 tick_once）
        self._step_errors = 0
        # 帧循环里合成器以外的环节出错的累计次数（出错不退出循环，只按次数稀疏记日志）
        self._loop_errors = 0
        self._pending: "OrderedDict[str, Tuple[str, float, int]]" = OrderedDict()
        self._playing: Set[str] = set()
        self._tool_seq = 0
        self._last_chat_ms = -(10**12)
        # 平台登记的动作片段（模型作者做好的动画、贴纸等）：来源 → (列名字, 按名字触发)
        self._clips: Dict[str, Tuple[Callable[[], List[str]], Callable[[str], Awaitable[bool]]]] = {}
        self._loop_task: Optional[asyncio.Task] = None
        self._started = False
        # 最近若干帧的推进时刻：算实际帧率与卡顿（和决策循环抢同一个事件循环时，形象会顿一下）
        self._tick_times: Deque[int] = deque(maxlen=self.TICK_HISTORY)
        # 跟着下一句做的动作：[动作名, 记下的时刻, 绑定的句子（还没定是哪句时为 None）]
        self._deferred: List[List[Any]] = []

    def _subscriptions(self) -> List[Tuple[str, Callable[..., Any], type]]:
        """订阅清单：(事件名, 处理函数, 载荷类)，订阅与退订共用。"""
        room = [
            (name, self._on_room_message, RoomMessagePayload)
            for name in (
                CoreEvents.ROOM_MESSAGE_DANMAKU,
                CoreEvents.ROOM_MESSAGE_GIFT,
                CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
                CoreEvents.ROOM_MESSAGE_GUARD,
            )
        ]
        game = [
            (name, self._on_game, GamePayload)
            for name in (CoreEvents.GAME_MILESTONE, CoreEvents.GAME_ATTENTION_REQUIRED, CoreEvents.GAME_ERROR)
        ]
        return [
            (CoreEvents.STREAMER_SPEECH, self._on_speech, StreamerSpeechPayload),
            (CoreEvents.TTS_UTTERANCE_STARTED, self._on_started, UtteranceStartedPayload),
            (CoreEvents.TTS_UTTERANCE_FINISHED, self._on_finished, UtteranceFinishedPayload),
            (CoreEvents.TTS_UTTERANCE_FAILED, self._on_failed, UtteranceFailedPayload),
            (CoreEvents.STREAMER_STAGE, self._on_stage, StreamerStagePayload),
            (CoreEvents.PLANNER_DECISION, self._on_decision, PlannerDecisionPayload),
            *room,
            *game,
        ]

    # ===== 生命周期 =====

    async def start(self) -> None:
        """订阅事件、接上口型信号（幂等）。帧循环在第一个驱动挂上时才开始跑。"""
        if self._started:
            return
        self._started = True
        bus = self._event_bus
        if bus is not None:
            for name, handler, model in self._subscriptions():
                bus.on(name, handler, model_class=model)
        if self._lipsync_analyzer is not None:
            self._lipsync_analyzer.add_renderer(self._mouth_tap)
        self.logger.info(f"面捕合成器已启动（{self._config.tick_hz} 帧/秒，TTS {'开' if self._tts_enabled else '关'}）")
        self._ensure_loop()

    async def stop(self) -> None:
        """退订、摘掉口型信号、停帧循环，并给还挂着的驱动各发一帧静止姿态（幂等）。"""
        if not self._started:
            return
        self._started = False
        bus = self._event_bus
        if bus is not None:
            for name, handler, _ in self._subscriptions():
                try:
                    bus.off(name, handler)
                except Exception as exc:  # noqa: BLE001 - 退订失败不阻断关停
                    self.logger.debug(f"{name} 退订失败（已忽略）: {exc}")
        if self._lipsync_analyzer is not None:
            try:
                self._lipsync_analyzer.remove_renderer(self._mouth_tap)
            except Exception as exc:  # noqa: BLE001 - 摘除失败不阻断关停
                self.logger.debug(f"口型信号摘除失败（已忽略）: {exc}")
        await self._stop_loop()
        self._deferred.clear()
        for driver in list(self._drivers):
            await self._send_rest(driver)
        self._drivers.clear()

    async def attach(self, driver: TrackingDriver) -> None:
        """挂上一个平台驱动（重复挂载幂等）；有驱动时帧循环才跑。"""
        if driver not in self._drivers:
            self._drivers.append(driver)
            self.logger.info(f"面捕合成器已挂上驱动: {driver.name}")
        self._ensure_loop()

    async def detach(self, driver: TrackingDriver) -> None:
        """摘下一个驱动：先发一帧静止姿态再摘（没有驱动了就停帧循环）。"""
        if driver not in self._drivers:
            return
        await self._send_rest(driver)
        self._drivers.remove(driver)
        if not self._drivers:
            await self._stop_loop()

    # ===== 帧循环 =====

    async def tick_once(self) -> Frame:
        """推进一帧并发给所有就绪的驱动（帧循环每拍调一次；测试可直接调）。"""
        now = self._clock()
        self._tick_times.append(now)
        if self._deferred:
            await self._perform_deferred(lambda entry: now - entry[1] >= self.DEFERRED_ACT_MAX_WAIT_MS)
        try:
            frame = self.synthesizer.step(now)
        except Exception:  # noqa: BLE001 - 合成器出错不能让形象停在半截表情上
            frame = self._restart_synthesizer()
        for driver in list(self._drivers):
            if not driver.ready():
                continue
            try:
                await driver.apply(frame)
            except Exception:  # noqa: BLE001 - 单个驱动失败不影响其他驱动与下一帧
                count = self._driver_errors.get(driver.name, 0) + 1
                self._driver_errors[driver.name] = count
                if count == 1 or count % 300 == 0:
                    self.logger.exception(f"驱动 {driver.name} 写入失败（累计 {count} 次，之后每 300 次记一条）")
        return frame

    def _restart_synthesizer(self) -> Frame:
        """合成器推进一帧出错：换一台全新的（同一份调参），这一帧发静止姿态。

        平台在停止注入后会一直保持最后的值，合成器要是就此停住，脸会定在出错那一刻的半截表情上，
        直到下播。所以出错就从静止姿态重新长出来：正在播的那句照样算"在说话"，正在做的表情、
        心情和在想的状态丢掉，等下一个事件再接上。每次出错都换，日志按次数稀疏记。
        """
        self._step_errors += 1
        if self._step_errors == 1 or self._step_errors % 300 == 0:
            self.logger.exception(f"面捕合成器推进一帧出错，已换一台新的从静止姿态重来（累计 {self._step_errors} 次）")
        wander = self.synthesizer.head_wander_enabled
        self.synthesizer = TrackingSynthesizer(self._config, rng=self._rng)
        self.synthesizer.set_head_wander(wander)
        self.synthesizer.set_speaking(bool(self._playing))
        return self.synthesizer.rest_frame()

    async def _run_loop(self) -> None:
        while True:
            # 每拍现读帧率：调校时改了帧率不用重启循环
            period = 1.0 / self._config.tick_hz
            started = time.perf_counter()
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001 - 某一拍出错只跳过这一拍，循环一退出形象就停住了
                self._loop_errors += 1
                if self._loop_errors == 1 or self._loop_errors % 300 == 0:
                    self.logger.exception(f"面捕合成器帧循环这一拍出错，已跳过（累计 {self._loop_errors} 次）")
            try:
                await asyncio.sleep(max(0.0, period - (time.perf_counter() - started)))
            except asyncio.CancelledError:
                return

    def _ensure_loop(self) -> None:
        """已启动、有驱动、允许自跑时拉起帧循环（不论驱动先挂还是运行时先启动）。"""
        if self._run_loop_enabled and self._started and self._drivers and self._loop_task is None:
            self._loop_task = asyncio.create_task(self._run_loop(), name="TrackingRuntime.loop")

    async def _stop_loop(self) -> None:
        task = self._loop_task
        self._loop_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _send_rest(self, driver: TrackingDriver) -> None:
        if not driver.ready():
            return
        try:
            await driver.apply(self.synthesizer.rest_frame())
        except Exception:  # noqa: BLE001 - 收尾失败只记日志
            self.logger.exception(f"驱动 {driver.name} 发送静止姿态失败")

    # ===== 主动调用（工具、调试台） =====

    def hold_expression(self, emotion: str, intensity: float, seconds: float) -> str:
        """主播有意摆一个表情并保持 ``seconds`` 秒（演"假装生气一会儿"这类梗）。"""
        self._tool_seq += 1
        now = self._clock()
        return self.synthesizer.express(
            emotion, intensity, now, key=f"hold-{self._tool_seq}", release_at_ms=now + int(seconds * 1000)
        )

    def register_clips(
        self, source: str, names: Callable[[], List[str]], trigger: Callable[[str], Awaitable[bool]]
    ) -> None:
        """平台登记自己的动作片段（同一来源重复登记即覆盖）；名字现取，换模型后自动更新。"""
        self._clips[source] = (names, trigger)

    def unregister_clips(self, source: str) -> None:
        self._clips.pop(source, None)

    def action_catalog(self) -> List[Dict[str, str]]:
        """主播可以主动发起的动作目录：有意的动作、保持表情、各平台登记的片段。"""
        catalog = [{"name": name, "kind": "动作", "description": g.description} for name, g in GESTURES.items()]
        catalog.append(
            {
                "name": HOLD_EXPRESSION,
                "kind": "表情",
                "description": "摆一个表情并保持一会儿（同时给 emotion 与 seconds）",
            }
        )
        for source, (names, _) in self._clips.items():
            try:
                clip_names = names()
            except Exception:  # noqa: BLE001 - 单个来源列目录失败不影响其他来源
                self.logger.exception(f"列 {source} 动作片段失败（已跳过）")
                continue
            catalog.extend(
                {"name": n, "kind": f"{source} 片段", "description": "模型作者做好的动作"} for n in clip_names
            )
        return catalog

    async def act(self, action: str, *, with_next_utterance: bool = False) -> Optional[bool]:
        """执行一个动作：有意的动作优先，其次按名字找平台片段。未知名字返回 None。

        ``with_next_utterance``：主播在想下一句时发来的动作先记下，等那句开口再做（见模块说明）；
        不在想、没开语音时照常立刻做。记下即返回 True。
        """
        if with_next_utterance and self._tts_enabled and self.synthesizer.thinking:
            if not self._known_action(action):
                return None
            self._deferred.append([action, self._clock(), None])
            return True
        return await self._perform(action)

    def _known_action(self, action: str) -> bool:
        if action in GESTURES:
            return True
        for source, (names, _) in self._clips.items():
            try:
                if action in names():
                    return True
            except Exception:  # noqa: BLE001 - 单个来源列目录失败不影响其他来源
                self.logger.exception(f"列 {source} 动作片段失败（已跳过）")
        return False

    async def _perform_deferred(self, due: Callable[[List[Any]], bool]) -> None:
        """把记下的动作里到点的做出来（按记下的先后）。"""
        ready = [entry for entry in self._deferred if due(entry)]
        if not ready:
            return
        self._deferred = [entry for entry in self._deferred if not due(entry)]
        for action, _, _ in ready:
            await self._perform(action)

    async def _perform(self, action: str) -> Optional[bool]:
        gesture = GESTURES.get(action)
        if gesture is not None:
            now = self._clock()
            if action == LOOK_AT_CHAT:
                return self.synthesizer.glance_at_chat(now, duration_ms=LOOK_AT_CHAT_MS, min_gap_ms=0)
            for offset_ms, kind, scale in gesture.steps:
                self.synthesizer.pulse(kind, now + offset_ms, scale)
            return True
        for source, (names, trigger) in self._clips.items():
            try:
                if action in names():
                    return bool(await trigger(action))
            except Exception:  # noqa: BLE001 - 片段触发失败按未成功返回
                self.logger.exception(f"触发 {source} 动作片段失败: {action}")
                return False
        return None

    def express(self, emotion: str, intensity: float) -> str:
        """主播调工具设表情：立刻上脸，保持配置的时长后回落。返回调度结果。"""
        self._tool_seq += 1
        now = self._clock()
        return self.synthesizer.express(
            emotion,
            intensity,
            now,
            key=f"tool-{self._tool_seq}",
            release_at_ms=now + self._config.expression_tool_hold_ms,
        )

    def set_head_wander(self, enabled: bool) -> None:
        self.synthesizer.set_head_wander(enabled)

    @property
    def head_wander_enabled(self) -> bool:
        return self.synthesizer.head_wander_enabled

    def current_emotion(self) -> str:
        return self.synthesizer.current_emotion(self._clock())

    def mood(self) -> tuple[float, float]:
        """此刻的心情（愉快度, 兴奋度）。"""
        return self.synthesizer.mood(self._clock())

    def rest_frame(self) -> Frame:
        """人设静止姿态（驱动在断开前自行发送，形象不停在半闭眼之类的姿势上）。"""
        return self.synthesizer.rest_frame()

    @property
    def driver_names(self) -> List[str]:
        return [driver.name for driver in self._drivers]

    # ===== 调校（形象调校页） =====

    @property
    def config(self) -> TrackingConfig:
        """当前生效的调参（调校页改过就是改后的值，未必和配置文件一致）。"""
        return self._config

    def reconfigure(self, config: TrackingConfig) -> None:
        """换一份调参并立刻生效，不重启、不打断正在进行的表情与心情。"""
        self._config = config
        self.synthesizer.reconfigure(config)

    def recipes(self) -> Dict[str, Recipe]:
        """生效的表情配方（默认 + 人设覆盖）。"""
        return self.synthesizer.recipes()

    def set_manual(self, values: Frame, hold_ms: int) -> None:
        """手动摆姿：指定的通道保持这些值 ``hold_ms`` 毫秒（页面在调时不断续期）。"""
        self.synthesizer.set_manual(values, self._clock() + hold_ms)

    def clear_manual(self) -> None:
        self.synthesizer.clear_manual()

    def look_at(self, x: float, y: float, duration_ms: int) -> None:
        """看向某一点一会儿（调校页拖眼神目标时预览：头会跟着转，和真的看过去一样）。"""
        self.synthesizer.look_at((x, y), self._clock(), duration_ms=duration_ms)

    def loop_stats(self) -> Dict[str, float]:
        """最近若干帧的实际帧率、最长帧间隔，以及间隔超过两倍帧周期的次数（卡顿）。"""
        times = list(self._tick_times)
        if len(times) < 2:
            return {"fps": 0.0, "max_gap_ms": 0.0, "stalls": 0}
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
        period_ms = 1000.0 / self._config.tick_hz
        span = times[-1] - times[0]
        return {
            "fps": round(len(gaps) * 1000.0 / span, 1) if span > 0 else 0.0,
            "max_gap_ms": float(max(gaps)),
            "stalls": sum(1 for gap in gaps if gap > 2 * period_ms),
        }

    def snapshot(self) -> Dict[str, Any]:
        """此刻的身体状态（调校页实时显示）：最近一帧、主表情、心情、在想 / 在说、手动摆姿与驱动。"""
        now = self._clock()
        synthesizer = self.synthesizer
        valence, arousal = synthesizer.mood(now)
        return {
            "frame": synthesizer.last_frame or synthesizer.rest_frame(),
            "emotion": synthesizer.current_emotion(now),
            "mood": {"valence": valence, "arousal": arousal},
            "thinking": synthesizer.thinking,
            "speaking": synthesizer.speaking,
            "manual": synthesizer.manual_active(now),
            "drivers": self.driver_names,
            "running": self._loop_task is not None,
            "loop": self.loop_stats(),
        }

    def calibrations(self) -> Dict[str, Dict[str, float]]:
        """各驱动的换算校准项当前值（驱动名 → 校准项 → 值）；没有校准项的驱动不列。"""
        return {
            driver.name: dict(driver.calibration())
            for driver in self._drivers
            if isinstance(driver, CalibratableDriver)
        }

    def calibrate(self, driver_name: str, values: Mapping[str, float]) -> bool:
        """改某个驱动的校准项并立刻生效；没有这个驱动或它不支持校准时返回 False。

        校准项名字不认识时由驱动抛 ValueError（调用方据此报错）。
        """
        for driver in self._drivers:
            if driver.name == driver_name and isinstance(driver, CalibratableDriver):
                driver.calibrate(values)
                return True
        return False

    # ===== 事件 =====

    def _estimate_ms(self, text: str) -> int:
        return max(1500, len(text) * self._config.speech_ms_per_char)

    async def _on_speech(self, event_name: str, payload: StreamerSpeechPayload, source: str) -> None:
        try:
            now = self._clock()
            if not self._tts_enabled:
                # 没有语音：发言即开口，按字数估算说多久
                release_at = now + self._estimate_ms(payload.text) + self._config.expression_linger_ms
                self.synthesizer.express(
                    payload.emotion, payload.emotion_intensity, now, key=payload.utterance_id, release_at_ms=release_at
                )
                return
            # 有语音：先记下，等真正开始播放再上脸
            self._pending[payload.utterance_id] = (payload.emotion, payload.emotion_intensity, now)
            while len(self._pending) > self.PENDING_LIMIT:
                self._pending.popitem(last=False)
            for uid in [uid for uid, (_, _, at) in self._pending.items() if now - at > self.PENDING_TTL_MS]:
                del self._pending[uid]
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"发言情绪记录异常（已忽略）: utterance_id={getattr(payload, 'utterance_id', '?')}")

    async def _on_started(self, event_name: str, payload: UtteranceStartedPayload, source: str) -> None:
        try:
            now = self._clock()
            self._playing.add(payload.utterance_id)
            self.synthesizer.set_speaking(True)
            # 开口即想完了：思考神情散去、视线回到镜头（阶段事件可能稍晚才到）
            self.synthesizer.end_thinking(now)
            entry = self._pending.pop(payload.utterance_id, None)
            if entry is not None:
                emotion, intensity, _ = entry
                expected = payload.duration_ms or self._estimate_ms(payload.speech_text)
                release_at = now + expected + self._config.expression_linger_ms + self.SAFETY_MARGIN_MS
                self.synthesizer.express(emotion, intensity, now, key=payload.utterance_id, release_at_ms=release_at)
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"开播表情处理异常（已忽略）: utterance_id={getattr(payload, 'utterance_id', '?')}")
        try:
            # 绑在这句话上的动作，开口时做（这句有没有带情绪都一样）
            await self._perform_deferred(lambda entry: entry[2] == payload.utterance_id)
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"开口时补做动作异常（已忽略）: utterance_id={getattr(payload, 'utterance_id', '?')}")

    async def _on_finished(self, event_name: str, payload: UtteranceFinishedPayload, source: str) -> None:
        self._end_utterance(payload.utterance_id, linger=True)

    async def _on_failed(self, event_name: str, payload: UtteranceFailedPayload, source: str) -> None:
        self._pending.pop(payload.utterance_id, None)
        self._end_utterance(payload.utterance_id, linger=False)
        try:
            # 这句没播出来，绑在它上面的动作不再等
            await self._perform_deferred(lambda entry: entry[2] == payload.utterance_id)
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"播放失败后补做动作异常（已忽略）: utterance_id={payload.utterance_id}")

    async def _on_decision(self, event_name: str, payload: PlannerDecisionPayload, source: str) -> None:
        """一轮决策结束：要说话就把还没着落的动作绑到这句话上，不说话就立刻做。"""
        try:
            for entry in self._deferred:
                if entry[2] is None and payload.should_reply and payload.utterance_id:
                    entry[2] = payload.utterance_id
            await self._perform_deferred(lambda entry: entry[2] is None)
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"决策结束后处理动作异常（已忽略）: round_id={getattr(payload, 'round_id', '?')}")

    async def _on_stage(self, event_name: str, payload: StreamerStagePayload, source: str) -> None:
        try:
            now = self._clock()
            if payload.stage == "planning":
                recent_chat = now - self._last_chat_ms <= self.READ_WINDOW_MS
                self.synthesizer.begin_thinking(now, read_first_ms=self._config.reading_ms if recent_chat else 0)
            elif payload.stage == "idle":
                self.synthesizer.end_thinking(now)
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"阶段事件处理异常（已忽略）: stage={getattr(payload, 'stage', '?')}")

    async def _on_room_message(self, event_name: str, payload: RoomMessagePayload, source: str) -> None:
        try:
            now = self._clock()
            self._last_chat_ms = now
            if not self._config.room_reactions_enabled:
                return
            kind = payload.message_type
            if kind == "danmaku":
                if not self.synthesizer.thinking and not self._playing:
                    duration, gap = self.DANMAKU_GLANCE
                    self.synthesizer.glance_at_chat(now, duration_ms=duration, min_gap_ms=gap)
            elif kind in self.PAID_BRIGHTEN:
                duration, gap = self.PAID_GLANCE
                self.synthesizer.glance_at_chat(now, duration_ms=duration, min_gap_ms=gap)
                self.synthesizer.pulse("brighten", now, self.PAID_BRIGHTEN[kind])
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"直播间消息反应异常（已忽略）: {event_name}")

    async def _on_game(self, event_name: str, payload: GamePayload, source: str) -> None:
        try:
            if not self._config.game_reactions_enabled:
                return
            reaction = self.GAME_PULSES.get(payload.event_type)
            if reaction is not None:
                self.synthesizer.pulse(reaction[0], self._clock(), reaction[1])
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"游戏事件反应异常（已忽略）: {event_name}")

    def _end_utterance(self, utterance_id: str, *, linger: bool) -> None:
        try:
            now = self._clock()
            self._playing.discard(utterance_id)
            self.synthesizer.set_speaking(bool(self._playing))
            delay = self._config.expression_linger_ms if linger else 0
            self.synthesizer.release(utterance_id, now + delay)
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            self.logger.exception(f"发声收尾处理异常（已忽略）: utterance_id={utterance_id}")


__all__ = ["TrackingDriver", "TrackingRuntime"]
