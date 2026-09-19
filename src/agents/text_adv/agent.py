"""TextAdvGameAgent —— 视觉小说游戏 Agent（观察循环 + auto 标志）

归类：游戏 Agent（命令驱动）。``set_auto`` 命令即过程载体——翻动 Agent 持有的
auto 标志，观察循环（``asyncio.Task``）由 Agent 自己拉起/停止；循环存续期间
持续感知屏幕并上报，命令停止后任务退出、空闲零消耗。

职责边界：
- 只感知与上报：新屏发 ``game.milestone``，选项屏发 ``game.report``
  （escalation 语义，引导主播调 ``text_adv_choose``）；绝不替主播做选择
- 感知失败（采集异常 / 空图 / 解析失败）只记日志，不污染事件流；
  循环整体异常退出才发 ``game.error`` 并回落 auto 标志
- 窗口护栏：循环启动时查找并持有目标窗口，每轮检查前台焦点与几何；
  未通过则停循环并发一条 ``game.error``（不刷屏）
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Protocol, Tuple

from src.modules.agents.base import BaseAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.logging import get_logger
from src.modules.tools import ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.vision.look_at_screen import ScreenCaptureResult

from .config import TextAdvConfig
from .input import InputBackend
from .screen import StableFrameResult, frame_key, text_key, wait_stable
from .state import TextAdvGameAgentState
from .vlm import ScreenReading, VisionReader
from .window import WindowBackend


__all__ = [
    "TextAdvGameAgent",
    "build_text_adv_agent",
]


class FrameCapture(Protocol):
    """观察循环所需的帧采集能力（与 screen 模块稳定判定约定的调用面同形）。"""

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        """截取一次屏幕快照；``image=None`` 表示本次采集失败。"""
        ...


# 上报正文的摘要长度上限（字符），避免 report message 过长
_BODY_SUMMARY_MAX_CHARS = 120


class TextAdvGameAgent(BaseAgent):
    """文字冒险（视觉小说）游戏 Agent。

    - auto 标志由本 Agent 持有；观察循环是 Agent 自身生命周期的一部分，
      工具侧只经 ``set_auto`` 翻标志，绝不持有循环
    - 感知依赖构造注入：``vision_reader``（读屏）、``capture``（帧采集，
      供稳定判定）、``window_backend``（窗口护栏）
    - 事件上报走基类 ``emit_event``（event_bus 为 None 时静默跳过）
    """

    # ----- 元数据 -----
    name = "text_adv"
    description = "文字冒险游戏 Agent（视觉小说观察与上报）"

    # ----- 事件族声明 -----
    emits_events = (
        CoreEvents.GAME_MILESTONE,
        CoreEvents.GAME_REPORT,
        CoreEvents.GAME_ERROR,
    )

    def __init__(
        self,
        config: TextAdvConfig,
        *,
        vision_reader: VisionReader,
        window_backend: WindowBackend,
        capture: FrameCapture,
        input_backend: InputBackend,
        tool_registry: Optional[ToolRegistry] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        """构造注入全部依赖。

        Args:
            config: TextAdvConfig 实例（采样/超时/去重上限等运行参数）
            vision_reader: 读屏后端（读一屏返回结构化结果）
            window_backend: 窗口后端（查找/夺焦/前台/几何）
            capture: 帧采集后端（稳定判定与帧级去重的数据源）
            input_backend: 键鼠注入后端（工具面动作出口的触达通道）
            tool_registry: 可选 ToolRegistry（启动期注册工具面；
                未注入时工具面不注册，调用期按注册缺失降级）
            event_bus: 可选 EventBus（game.* 事件发射）
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._vision_reader = vision_reader
        self._window_backend = window_backend
        self._capture = capture
        self._tool_registry = tool_registry
        # 函数内 import：tools 模块反向引用本模块的 TextAdvGameAgent（循环 import 规避）
        from .tools import TextAdvToolProvider

        self._tool_provider: TextAdvToolProvider = TextAdvToolProvider(agent=self, input_backend=input_backend)

        # 内容状态（避免与 BaseAgent.state 属性同名故用 _game_state）
        self._game_state = TextAdvGameAgentState(max_recent_screens=config.max_recent_screens)

        # auto 标志与观察循环任务（本 Agent 持有；工具只经 set_auto 翻标志）
        self._auto: bool = False
        self._observe_task: Optional["asyncio.Task[None]"] = None

        self._logger = get_logger("TextAdvGameAgent")
        self._logger.info("TextAdvGameAgent 已构造（观察循环待 set_auto 命令启动）")

    # ==================================================================
    # auto 命令入口
    # ==================================================================

    @property
    def auto(self) -> bool:
        """当前是否处于自动观察模式（测试与状态导出可读）。"""
        return self._auto

    async def set_auto(self, on: bool) -> None:
        """启停自动观察（幂等命令入口；工具侧只调这里翻标志）。

        True：置位标志并确保恰好一个观察循环任务在跑（已有存活任务则不动）。
        False：清标志并取消等待观察任务退出。
        """
        if on:
            self._auto = True
            if self._observe_task is not None and not self._observe_task.done():
                self._logger.debug("set_auto(True)：观察循环已在运行，忽略重复启动")
                return
            self._observe_task = asyncio.create_task(self._observe_loop(), name="text_adv-observe")
            self._logger.info("观察循环已启动（auto=True）")
            return
        self._auto = False
        task = self._observe_task
        self._observe_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # 预期取消路径
        except Exception as exc:  # noqa: BLE001 - 退出异常不阻断停机
            self._logger.warning(f"观察循环退出异常: {type(exc).__name__}: {exc}")
        self._logger.info("观察循环已停止（auto=False）")

    # ==================================================================
    # 观察循环
    # ==================================================================

    async def _observe_loop(self) -> None:
        """观察循环主体：窗口护栏 → 等稳定 → 帧级去重 → VLM 读取 → 文本去重 → 上报。

        退出语义：
        - 窗口护栏未通过：记日志 + 一条 ``game.error`` + auto 回落 False，循环终止
        - 感知失败（采集异常/空图/解析失败）：只记日志，循环继续
        - 整体异常退出：记日志（带堆栈）+ 恰好一条 ``game.error`` + auto 回落 False
        """
        try:
            win = self._window_backend.find(self.typed_config.game_window_title_keyword)
            if win is None:
                await self._guard_failure(
                    f"未找到游戏窗口（标题关键词={self.typed_config.game_window_title_keyword!r}）"
                )
                return
            if not self._window_backend.focus(win):
                self._logger.warning("游戏窗口夺焦失败，继续以前台检查作为护栏")
            region = self._capture_region()
            sample_s = self.typed_config.stability_sample_ms / 1000

            # 双层去重与降频的循环内状态
            last_frame: Optional[str] = None
            last_text: Optional[str] = None
            no_new_text_rounds: int = 0
            vlm_paused: bool = False
            pause_still_seen: bool = False

            while True:
                if not self._window_backend.is_foreground(win):
                    await self._guard_failure("游戏窗口失去前台焦点")
                    return
                if self._window_backend.rect(win) is None:
                    await self._guard_failure("窗口几何不可得（窗口可能已关闭）")
                    return

                if vlm_paused:
                    # 降频段：只做廉价帧检查，不消耗 VLM。恢复条件是画面
                    # 先静止（动画播完）再变化——常驻动画期间帧持续变化，
                    # 不构成恢复依据，避免烧 VLM。
                    snapshot = self._capture.capture(self.typed_config.monitor_index, region)
                    image = snapshot.image if snapshot is not None else None
                    if image:
                        fingerprint = frame_key(image)
                        if fingerprint == last_frame:
                            pause_still_seen = True
                        elif pause_still_seen and fingerprint != last_frame:
                            self._logger.info("画面静止后再次变化，恢复 VLM 读取")
                            vlm_paused = False
                            pause_still_seen = False
                            no_new_text_rounds = 0
                            last_frame = None  # 恢复后强制走一次完整读取
                        else:
                            last_frame = fingerprint
                    await asyncio.sleep(sample_s)
                    continue

                stable = await self._wait_stable_quietly(region)
                if stable is None or not stable.image:
                    await asyncio.sleep(sample_s)
                    continue

                fingerprint = frame_key(stable.image)
                prev_frame = last_frame
                last_frame = fingerprint

                if prev_frame is not None and fingerprint == prev_frame:
                    await asyncio.sleep(sample_s)
                    continue  # 帧未变：零 VLM 消耗

                reading = await self._vision_reader.read_screen(want_options=True)
                if not reading.parse_ok:
                    self._logger.warning(f"读屏解析失败（本轮跳过）: {reading.parse_error}")
                    await asyncio.sleep(sample_s)
                    continue

                fingerprint_text = text_key(reading.text)
                if last_text is not None and fingerprint_text == last_text:
                    no_new_text_rounds += 1
                    if no_new_text_rounds >= self.typed_config.no_change_limit:
                        self._logger.info(f"文本连续 {no_new_text_rounds} 轮无新屏，暂停 VLM 读取只做帧级检查")
                        vlm_paused = True
                        pause_still_seen = False
                    await asyncio.sleep(sample_s)
                    continue

                last_text = fingerprint_text
                no_new_text_rounds = 0
                self._game_state.record_screen(reading.text, reading.options)
                if reading.options:
                    await self._emit_report(reading)
                else:
                    await self._emit_milestone(reading)
                await asyncio.sleep(sample_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 循环整体死亡路径
            self._logger.exception(f"观察循环异常退出: {type(exc).__name__}: {exc}")
            self._auto = False
            try:
                await self.emit_error(f"观察循环异常退出: {type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 - 错误上报失败不再二次上抛
                self._logger.exception("观察循环死亡的 game.error 上报也失败")

    async def _wait_stable_quietly(self, region: Optional[Tuple[int, int, int, int]]) -> Optional[StableFrameResult]:
        """等待画面稳定；采集后端抛异常按感知失败降级（返回 None，不外抛）。"""
        try:
            return await wait_stable(
                self._capture,
                region,
                self.typed_config.monitor_index,
                sample_ms=self.typed_config.stability_sample_ms,
                consecutive=self.typed_config.stability_consecutive,
                timeout_ms=self.typed_config.stability_timeout_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 采集异常是感知失败，不是循环死亡
            self._logger.warning(f"稳定判定期间采集异常（本轮跳过）: {type(exc).__name__}: {exc}")
            return None

    async def _guard_failure(self, reason: str) -> None:
        """窗口护栏未通过：停循环 + 恰好一条 game.error + auto 回落 False。"""
        self._logger.error(f"窗口护栏未通过，停止观察循环: {reason}")
        self._auto = False
        await self.emit_error(f"自动观察已停止：{reason}")

    def _capture_region(self) -> Optional[Tuple[int, int, int, int]]:
        """把配置区域 [x, y, w, h] 换算为采集契约的 (x1, y1, x2, y2)；未配置返回 None。"""
        raw = self.typed_config.region
        if raw is None or len(raw) != 4:
            return None
        x, y, w, h = raw
        return (x, y, x + w, y + h)

    # ==================================================================
    # 事件上报（game.* 语义域）
    # ==================================================================

    async def _emit_milestone(self, reading: ScreenReading) -> None:
        """新叙事屏上报：``game.milestone``（message 自述当前屏文本）。"""
        summary = (
            reading.text
            if len(reading.text) <= _BODY_SUMMARY_MAX_CHARS
            else reading.text[:_BODY_SUMMARY_MAX_CHARS] + "…"
        )
        payload = GamePayload(
            game=self.name,
            event_type="milestone",
            message=f"剧情推进到新一屏：{summary}",
        )
        await self.emit_event(CoreEvents.GAME_MILESTONE, payload)

    async def _emit_report(self, reading: ScreenReading) -> None:
        """选项屏上报：``game.report``（escalation 语义，引导主播调 choose 工具）。"""
        summary = (
            reading.text
            if len(reading.text) <= _BODY_SUMMARY_MAX_CHARS
            else reading.text[:_BODY_SUMMARY_MAX_CHARS] + "…"
        )
        listed = "\n".join(f"{i}. {opt.label}" for i, opt in enumerate(reading.options, start=1))
        message = (
            f"画面出现待定夺的选项。当前正文：{summary}\n"
            f"选项：\n{listed}\n"
            "请调用 text_adv_choose(option=N) 告知选择（N 为选项序号）。"
        )
        payload = GamePayload(
            game=self.name,
            event_type="report",
            message=message,
            report_kind="escalation",
        )
        await self.emit_event(CoreEvents.GAME_REPORT, payload)

    async def emit_error(self, message: str) -> None:
        """emit ``game.error``（供循环死亡/护栏路径与测试使用）。"""
        payload = GamePayload(game=self.name, event_type="error", message=message)
        await self.emit_event(CoreEvents.GAME_ERROR, payload)

    # ==================================================================
    # 协议六项：工具提供（工具面由同包 tools 重写任务接入）
    # ==================================================================

    def list_tools(self) -> List[ToolSpec]:
        """声明工具面（provider="text_adv" 的四工具）。"""
        return list(self._tool_provider.list_tools())

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def _on_start(self) -> None:
        """启动钩子：注册工具面并置位运行标记；观察循环由 set_auto 命令拉起（空闲零消耗）。"""
        # 函数内 import：tools 模块反向引用本模块（循环 import 规避）
        from .tools import build_text_adv_visible_to

        count = self.register_tool_provider(
            self._tool_provider,
            registry=self._tool_registry,
            visible_to=build_text_adv_visible_to(),
        )
        self._logger.info(f"TextAdvGameAgent 已启动（工具面注册 {count} 个；等待 set_auto 命令）")

    async def _on_stop(self) -> None:
        """停止钩子：取消观察循环（若有）并摘除本 Agent 注册的工具面。"""
        await self.set_auto(False)
        removed = self.unregister_tool_providers()
        if removed:
            self._logger.info(f"TextAdvGameAgent 已停止（摘除 {removed} 个工具）")

    # ==================================================================
    # 状态导出
    # ==================================================================

    def get_state_snapshot(self) -> Dict[str, Any]:
        """导出状态快照（形状由 TextAdvGameAgentState.to_dict 唯一定义）。"""
        return self._game_state.to_dict(auto=self._auto)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def build_text_adv_agent(
    *,
    config: TextAdvConfig,
    vision_reader: VisionReader,
    window_backend: WindowBackend,
    capture: FrameCapture,
    input_backend: InputBackend,
    tool_registry: Optional[ToolRegistry] = None,
    event_bus: Optional[EventBus] = None,
) -> TextAdvGameAgent:
    """便捷构造函数（装配任务接线用；依赖显式传参，不隐式拉起任何后端）。

    Args:
        config: TextAdvConfig 实例
        vision_reader: 读屏后端（如 RegistryVisionReader）
        window_backend: 窗口后端（如 PyGetWindowBackend）
        capture: 帧采集后端（与屏幕线 ScreenCapture 协议同形）
        input_backend: 键鼠注入后端（如 PyAutoGuiInputBackend）
        tool_registry: 可选 ToolRegistry（启动期注册工具面）
        event_bus: 可选 EventBus（game.* 事件发射）

    Returns:
        未启动的 TextAdvGameAgent 实例（生命周期由调用方管理）
    """
    return TextAdvGameAgent(
        config,
        vision_reader=vision_reader,
        window_backend=window_backend,
        capture=capture,
        input_backend=input_backend,
        tool_registry=tool_registry,
        event_bus=event_bus,
    )
