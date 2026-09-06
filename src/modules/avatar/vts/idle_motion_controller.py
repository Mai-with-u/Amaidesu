"""
IdleMotionController - VTS 默认拟人 idle 动画控制器

在模型不说话时持续生成轻微头部/身体晃动，让 Live2D 形象更自然。
说话时自动暂停/衰减，避免与口型同步冲突。

运动生成采用"随机漫步"而非固定频率正弦：每个轴向随机目标点缓动，
到位后随机停留或随机走向下一个目标（节奏、时长、幅度均不规则），
更接近真人的微动作；身体按一定比例耦合头部运动，形成头身联动。
"""

import asyncio
import random
import time
from typing import Any, Callable, Coroutine, Dict, Optional

from src.modules.logging import get_logger


def _smootherstep(x: float) -> float:
    """smootherstep 缓动（0~1 两端速度与加速度均为 0，运动无顿挫）"""
    x = min(1.0, max(0.0, x))
    return x * x * x * (x * (x * 6 - 15) + 10)


class AxisWander:
    """单轴随机漫步器：随机目标 + 随机时长 + 随机停留。

    每段运动：从当前值缓动到随机目标值（时长随机）；到位后以一定概率
    停留随机时间（拟人的"静止间隙"），否则立即走向下一个随机目标。
    目标值偏向中心（三角分布），约 20% 概率出现一次较大幅度动作。
    """

    def __init__(
        self,
        rng: random.Random,
        *,
        min_duration: float,
        max_duration: float,
        pause_probability: float,
        max_pause: float,
        min_target: float = 0.3,
    ):
        self._rng = rng
        self._min_dur = min_duration
        self._max_dur = max_duration
        self._pause_prob = pause_probability
        self._max_pause = max_pause
        self._min_target = min_target

        self._from = 0.0
        self._to = 0.0
        self._seg_start = 0.0
        self._seg_dur = 1.0
        self._pause_until = -1.0
        self._initialized = False

    def _new_target(self) -> float:
        if self._rng.random() < 0.2:
            mag = self._rng.uniform(0.7, 1.0)  # 偶尔一次较大幅度
        else:
            mag = abs(self._rng.triangular(-1.0, 1.0, 0.0))  # 偏向中心
        mag = max(mag, self._min_target)
        return mag if self._rng.random() < 0.5 else -mag

    def _new_segment(self, t: float) -> None:
        self._from = self._to
        self._to = self._new_target()
        self._seg_start = t
        self._seg_dur = self._rng.uniform(self._min_dur, self._max_dur)

    def value(self, t: float) -> float:
        """t 时刻的归一化输出（约 -1.0 ~ 1.0）"""
        if not self._initialized:
            # 随机化初始进度，避免所有轴同时从 0 同步起步
            self._initialized = True
            self._new_segment(t)
            self._seg_start = t - self._rng.uniform(0.0, self._seg_dur)

        if self._pause_until > 0:
            if t < self._pause_until:
                return self._to
            # 停留结束，走向下一个目标
            self._pause_until = -1.0
            self._new_segment(t)
            return self._from

        x = (t - self._seg_start) / max(self._seg_dur, 0.05)
        if x >= 1.0:
            current = self._to
            if self._rng.random() < self._pause_prob:
                self._pause_until = t + self._rng.uniform(0.3, self._max_pause)
            else:
                self._new_segment(t)
            return current

        return self._from + (self._to - self._from) * _smootherstep(x)


class IdleMotionController:
    """VTS 默认 idle 动画控制器"""

    def __init__(
        self,
        *,
        logger_name: str,
        is_connected: Callable[[], bool],
        is_speaking: Callable[[], bool],
        set_parameter: Callable[[str, float], Coroutine[Any, Any, bool]],
        param_head_x: str = "HeadAngleX",
        param_head_y: str = "HeadAngleY",
        param_head_z: str = "HeadAngleZ",
        param_body_x: str = "BodyX",
        param_body_y: str = "BodyY",
        param_body_z: str = "BodyZ",
        head_amplitude: float = 0.05,
        body_amplitude: float = 0.02,
        speed: float = 1.0,
        update_interval_ms: float = 40.0,
        fade_speed: float = 0.15,
        head_enabled: bool = True,
        body_enabled: bool = True,
        speech_pause_enabled: bool = True,
        extra_params: Optional[Dict[str, float]] = None,
        extra_speed: Optional[float] = None,
        rng: Optional[random.Random] = None,
    ):
        self._logger_name = logger_name
        self.logger = get_logger(logger_name)
        self._is_connected = is_connected
        self._is_speaking = is_speaking
        self._set_parameter = set_parameter

        self._param_head_x = param_head_x
        self._param_head_y = param_head_y
        self._param_head_z = param_head_z
        self._param_body_x = param_body_x
        self._param_body_y = param_body_y
        self._param_body_z = param_body_z

        self._head_amplitude = max(0.0, head_amplitude)
        self._body_amplitude = max(0.0, body_amplitude)
        self._speed = max(0.1, speed)
        self._interval = max(0.02, update_interval_ms / 1000.0)
        self._fade_speed = max(0.05, min(1.0, fade_speed))
        self._head_enabled = head_enabled
        self._body_enabled = body_enabled
        self._speech_pause_enabled = speech_pause_enabled

        # 随机漫步器：每个轴独立的随机目标/时长/停留，运动节奏不规则。
        # speed 通过缩放时长与停留生效（speed>1 整体更快）。
        # 节奏取向：较快移动到目标位（短时长）+ 较长停留（拟人的"停顿感"）。
        self._rng = rng or random.Random()
        s = self._speed
        es = max(0.1, extra_speed) if extra_speed is not None else s
        self._wanders: Dict[str, AxisWander] = {
            "head_x": AxisWander(
                self._rng, min_duration=1.0 / s, max_duration=2.5 / s, pause_probability=0.55, max_pause=3.5 / s
            ),
            "head_y": AxisWander(
                self._rng, min_duration=1.2 / s, max_duration=3.0 / s, pause_probability=0.55, max_pause=3.5 / s
            ),
            "head_z": AxisWander(
                self._rng, min_duration=1.5 / s, max_duration=3.5 / s, pause_probability=0.60, max_pause=4.0 / s
            ),
            "body_x": AxisWander(
                self._rng, min_duration=2.0 / s, max_duration=5.0 / s, pause_probability=0.60, max_pause=4.5 / s
            ),
            "body_y": AxisWander(
                self._rng, min_duration=2.0 / s, max_duration=5.0 / s, pause_probability=0.60, max_pause=4.5 / s
            ),
            "body_z": AxisWander(
                self._rng, min_duration=2.5 / s, max_duration=6.0 / s, pause_probability=0.65, max_pause=5.0 / s
            ),
        }

        # 额外摆动参数（如自定义的袖子参数 SleeveRX/RY/LX/LY）：参数名 -> 幅度。
        # 速度独立于头/身体（extra_speed），便于只加快头身而袖子保持不变。
        self._extra_params: Dict[str, float] = {k: max(0.0, v) for k, v in (extra_params or {}).items() if k}
        for name in self._extra_params:
            self._wanders[f"extra:{name}"] = AxisWander(
                self._rng, min_duration=1.5 / es, max_duration=4.0 / es, pause_probability=0.50, max_pause=4.0 / es
            )

        # 常驻基线参数（如 MouthSmile=base_smile）：每个 tick 持续写入，
        # 防止外部（VTS 追踪/模型重载等）把一次性写入的值覆盖掉。
        # 说话期间（LipSync 接管表情）或被当前 Intent 表情占用时不写入。
        self._baseline_params: Dict[str, float] = {}
        self._baseline_overrides: set[str] = set()

        # 当前输出值（用于说话后平滑恢复）
        self._current_values: Dict[str, float] = {}
        # 目标基础值（未说话时）
        self._target_values: Dict[str, float] = {}
        # 说话期间使用 zero target
        self._zero_target = {name: 0.0 for name in self._all_param_names()}

        # 失败参数记录，避免重复刷屏日志
        self._failed_params: set[str] = set()

        self._task: Optional[asyncio.Task] = None
        self._start_time = time.time()
        self._running = False

        self._log_shared_params()

    def _all_param_names(self) -> list[str]:
        names = []
        if self._head_enabled:
            names.extend([self._param_head_x, self._param_head_y, self._param_head_z])
        if self._body_enabled:
            names.extend([self._param_body_x, self._param_body_y, self._param_body_z])
        names.extend(self._extra_params.keys())
        # head/body 可能共享同一参数名（如模型脸部与身体绑定同一 tracking 输入），去重
        return list(dict.fromkeys(n for n in names if n))

    def set_parameter_names(
        self,
        *,
        param_head_x: Optional[str] = None,
        param_head_y: Optional[str] = None,
        param_head_z: Optional[str] = None,
        param_body_x: Optional[str] = None,
        param_body_y: Optional[str] = None,
        param_body_z: Optional[str] = None,
    ) -> None:
        """在 VTS 可用参数名解析后，动态更新控制器使用的参数名。"""
        if param_head_x is not None:
            self._param_head_x = param_head_x
        if param_head_y is not None:
            self._param_head_y = param_head_y
        if param_head_z is not None:
            self._param_head_z = param_head_z
        if param_body_x is not None:
            self._param_body_x = param_body_x
        if param_body_y is not None:
            self._param_body_y = param_body_y
        if param_body_z is not None:
            self._param_body_z = param_body_z

        self._current_values.clear()
        self._target_values.clear()
        self._zero_target = {name: 0.0 for name in self._all_param_names()}
        self._failed_params.clear()
        self._log_shared_params()

    def set_baseline_params(self, params: Dict[str, float]) -> None:
        """设置常驻基线参数（如 MouthSmile=0.3），每 tick 持续维持。"""
        self._baseline_params = {k: float(v) for k, v in (params or {}).items() if k}

    def set_baseline_overrides(self, expressions: Dict[str, float]) -> None:
        """设置当前 Intent 占用的表情参数名：这些参数不写入基线值。"""
        self._baseline_overrides = set((expressions or {}).keys())

    def _head_param_names(self) -> list[str]:
        return [n for n in (self._param_head_x, self._param_head_y, self._param_head_z) if n]

    def _body_param_names(self) -> list[str]:
        return [n for n in (self._param_body_x, self._param_body_y, self._param_body_z) if n]

    def _log_shared_params(self) -> None:
        """head/body 使用相同参数名时提示：两路信号将叠加合并写入同一参数。"""
        if not (self._head_enabled and self._body_enabled):
            return
        shared = sorted(set(self._head_param_names()) & set(self._body_param_names()))
        if shared:
            self.logger.info(
                f"idle head/body 共享参数 {shared}（模型的脸部与身体绑定到同一 tracking 输入），"
                f"两路 idle 信号将叠加合并写入。"
            )

    def start(self) -> None:
        """启动 idle 动画后台循环"""
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._task = asyncio.create_task(self._loop())
        self._task.set_name(f"{self._logger_name}.idle_loop")
        self.logger.info("VTS idle 动画已启动")

    async def stop(self) -> None:
        """停止 idle 动画后台循环，并把参数归零"""
        if not self._running:
            return
        self._running = False

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

        # 把参数归零
        for name in self._all_param_names():
            if self._is_connected() and name:
                try:
                    await self._set_parameter(name, 0.0)
                except Exception as e:
                    self.logger.warning(f"idle 停止归零失败 {name}: {e}")
        self._current_values.clear()
        self._failed_params.clear()
        self.logger.info("VTS idle 动画已停止")

    async def _loop(self) -> None:
        """后台循环：持续生成并发送 idle 参数"""
        try:
            while self._running:
                await asyncio.sleep(self._interval)
                if not self._is_connected():
                    continue

                try:
                    is_speaking = self._is_speaking()
                except Exception as e:
                    self.logger.warning(f"获取说话状态失败: {e}")
                    is_speaking = False

                # 计算目标值
                if self._speech_pause_enabled and is_speaking:
                    targets = self._zero_target
                else:
                    targets = self._compute_targets(speaking=is_speaking)

                # 平滑插值到目标值
                for name, target in targets.items():
                    current = self._current_values.get(name, 0.0)
                    diff = target - current
                    if abs(diff) < 0.001:
                        new_value = target
                    else:
                        new_value = current + diff * self._fade_speed
                    self._current_values[name] = new_value

                    # 基线参数每 tick 强制写入，防止被外部覆盖后无法纠正
                    force = name in self._baseline_params
                    if force or abs(new_value - current) >= 0.0005 or abs(new_value) < 0.001:
                        if name in self._failed_params:
                            continue
                        try:
                            success = await self._set_parameter(name, new_value)
                            if not success:
                                self._failed_params.add(name)
                                self.logger.warning(
                                    f"idle 参数 {name} 设置未成功，可能是模型中不存在该参数；"
                                    f"已暂停对此参数的 idle 写入，请检查 [handlers.vts] idle 参数名配置。"
                                )
                        except Exception as e:
                            self._failed_params.add(name)
                            self.logger.warning(
                                f"idle 参数 {name} 写入失败: {e}。"
                                f"已暂停对此参数的 idle 写入，请检查模型是否支持该参数。"
                            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"idle 动画后台循环异常: {e}", exc_info=True)

    def _compute_targets(self, speaking: bool = False) -> Dict[str, float]:
        """计算当前时刻的目标 idle 参数值（随机漫步，归一化值 × 各轴幅度）

        Args:
            speaking: 是否正在说话。说话期间不写入基线表情参数
                      （此时表情由 LipSync/Intent 接管，避免互相打架）。
        """
        t = time.time() - self._start_time
        targets: Dict[str, float] = {}
        hx = hy = hz = 0.0

        if self._head_enabled:
            # 头部：随机目标点之间缓动，节奏不规则（动-停-动）
            hx = self._wanders["head_x"].value(t)
            hy = self._wanders["head_y"].value(t)
            hz = 0.6 * self._wanders["head_z"].value(t)
            targets[self._param_head_x] = hx * self._head_amplitude
            targets[self._param_head_y] = hy * self._head_amplitude * 0.6
            # 歪头（顺时针/逆时针旋转）需要更大角度才可见，且模型该通道平滑度高
            targets[self._param_head_z] = hz * self._head_amplitude * 2.5

        if self._body_enabled:
            # 身体：自身低频漫步 + 按 0.45 比例耦合头部波形（头动带动身体）
            couple = 0.45
            bx = 0.7 * self._wanders["body_x"].value(t) + couple * hx
            by = 0.5 * self._wanders["body_y"].value(t) + couple * hy
            bz = 0.3 * self._wanders["body_z"].value(t) + couple * hz
            # 部分模型把脸部与身体绑定到同一 tracking 输入（如都用 FaceAngleX），
            # 此时与头部信号叠加合并写入，而不是覆盖头部值。
            self._merge_target(targets, self._param_body_x, bx * self._body_amplitude)
            self._merge_target(targets, self._param_body_y, by * self._body_amplitude * 0.6)
            self._merge_target(targets, self._param_body_z, bz * self._body_amplitude * 0.4)

        # 额外摆动参数（如袖子）：独立随机漫步，幅度由配置给定
        for name, amplitude in self._extra_params.items():
            self._merge_target(targets, name, self._wanders[f"extra:{name}"].value(t) * amplitude)

        # 常驻基线参数（如 MouthSmile）：说话期间或被当前 Intent 表情占用时不写入
        if not speaking:
            for name, value in self._baseline_params.items():
                if name in targets or name in self._baseline_overrides:
                    continue
                targets[name] = value

        return targets

    def _merge_target(self, targets: Dict[str, float], name: str, value: float) -> None:
        """写入 body 目标值；参数名已被 head 占用时叠加并钳制，避免覆盖头部信号。"""
        if not name:
            return
        if name in targets:
            limit = self._head_amplitude + self._body_amplitude
            targets[name] = max(-limit, min(limit, targets[name] + value))
        else:
            targets[name] = value

    def get_stats(self) -> Dict[str, Any]:
        """返回统计信息"""
        return {
            "running": self._running,
            "head_enabled": self._head_enabled,
            "body_enabled": self._body_enabled,
            "current_values": dict(self._current_values),
        }
