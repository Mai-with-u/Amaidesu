"""文字冒险 Agent 工具面（text_adv_advance / choose / set_auto / get_state）

主播与游戏之间的全部接口，名单全部 ``["streamer"]``（名单映射由
:func:`build_text_adv_visible_to` 提供，注册处声明）。

工具语义要点：
- ``choose`` 与 ``set_auto`` 的动作出口是**鼠标点击**（目标作品实测：选项
  与 AUTO 按钮均不支持键盘）；坐标来自 VLM 识别 + :mod:`coords` 反算，
  不由调用方传入——主播只给语义序号
- ``choose`` 每次调用先重读当前屏再解析，不维护跨屏脏表；点击后必须
  验证画面变化，验证不过返回失败结果，绝不二次盲点
- ``set_auto`` 幂等：目标状态与当前一致时直接返回快照，不点按钮
  （AUTO 是切换按钮，二次点击反而会关掉）
- 感知失败（采集异常 / 空图 / 解析失败）→ ``success=True`` + ``text=""`` +
  ``structured_content["error"]``，不发 ``game.error``——感知失败不污染
  事件流是屏幕线的既有契约；只有观察循环整体死亡才发 ``game.error``
  （由 Agent 承担，工具绝不发事件）
- 观察循环由 Agent 自持（经 ``agent.set_auto`` 翻标志拉起/停止），工具
  绝不自持循环

对外全名由 ``ToolSpec.full_name`` 派生（``text_adv_<工具名>``），
工具名里不手写前缀。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Tuple

from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import BaseToolProvider

from .agent import TextAdvGameAgent
from .coords import vlm_xy_to_screen
from .input import InputBackend
from .screen import StableFrameResult, text_key, wait_stable
from .vlm import ScreenReading
from .window import WindowInfo

logger = get_logger("TextAdvTools")

# 提供者标识统一来源（ToolSpec.provider / 追溯用），避免字面量重复
PROVIDER_NAME = "text_adv"


# ---------------------------------------------------------------------------
# 监视器几何（坐标反算的显示器原点来源；可注入替换以便测试）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonitorGeometry:
    """单台显示器的虚拟桌面几何（物理像素）。

    Attributes:
        left: 显示器左上角在虚拟桌面坐标系中的绝对 X
        top: 显示器左上角在虚拟桌面坐标系中的绝对 Y
        width: 显示器宽度（物理像素）
        height: 显示器高度（物理像素）
    """

    left: int
    top: int
    width: int
    height: int


MonitorResolver = Callable[[int], Optional[MonitorGeometry]]


def _query_monitor_geometry(monitor_index: int) -> Optional[MonitorGeometry]:
    """按显示器编号查询虚拟桌面几何（mss 的 monitors 表，[0] 为全体桌面）。

    查询失败（库缺失 / 编号越界 / 异常）返回 ``None``，调用方据此判定
    坐标链路不可用并走拒绝路径。
    """
    try:
        import mss

        with mss.mss() as sct:
            monitors = sct.monitors
            idx = monitor_index + 1
            if idx >= len(monitors):
                logger.error(f"监视器原点查询失败：编号 {monitor_index} 越界（共 {len(monitors) - 1} 台）")
                return None
            mon = monitors[idx]
            return MonitorGeometry(
                left=int(mon["left"]),
                top=int(mon["top"]),
                width=int(mon["width"]),
                height=int(mon["height"]),
            )
    except Exception as exc:  # noqa: BLE001 - 坐标链路不可用按拒绝处理，不上抛
        logger.exception(f"监视器原点查询失败（monitor={monitor_index}）: {type(exc).__name__}: {exc}")
        return None


# ---------------------------------------------------------------------------
# ToolSpec 工厂
# ---------------------------------------------------------------------------

# 快照返回形状（advance / choose / set_auto / get_state 共用同一键集）
_SNAPSHOT_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "当前屏正文（感知失败时为空串）"},
        "options": {
            "type": "array",
            "description": "当前屏选项列表（index 为 1 起编号，choose 的入参语义）",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "label": {"type": "string"},
                    "clickable": {"type": "boolean"},
                },
            },
        },
        "auto": {"type": "boolean"},
        "updated_at_ms": {"type": "integer"},
    },
    "required": ["text", "options", "auto", "updated_at_ms"],
}


def build_advance_spec() -> ToolSpec:
    """``text_adv_advance`` 工具规格——推进一屏（按键 → 等稳定 → 识别 → 快照）"""
    return ToolSpec(
        name="advance",
        description=(
            "推进文字冒险游戏一屏：向游戏窗口发送推进键（默认 Space）并夺回窗口"
            "焦点，等画面稳定后识别当前屏，返回新快照（text/options/auto/"
            "updated_at_ms）。何时用：需要主动看下一屏剧情时（auto 未开或选项"
            "屏上想先看别的）。识别失败时 text 为空、error 字段说明原因。"
        ),
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema=_SNAPSHOT_OUTPUT_SCHEMA,
    )


def build_choose_spec() -> ToolSpec:
    """``text_adv_choose`` 工具规格——选择第 N 项（鼠标点击其坐标）"""
    return ToolSpec(
        name="choose",
        description=(
            "选择文字冒险游戏当前屏的第 N 个选项（1-based，与上报/快照中选项"
            "的 index 一致）。实现为鼠标点击该项中心坐标——只传语义序号，"
            "坐标由代码从识别结果反算，不要传坐标。何时用：收到选项上报，"
            "或用 text_adv_get_state 看到当前屏有选项并作出定夺后。点击后"
            "会验证画面已变化；未变化则返回失败且不重试，请先 get_state"
            " 确认再决策。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "option": {
                    "type": "integer",
                    "description": "目标选项序号（1 起，须在当前 options 列表中）",
                },
            },
            "required": ["option"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema=_SNAPSHOT_OUTPUT_SCHEMA,
    )


def build_set_auto_spec() -> ToolSpec:
    """``text_adv_set_auto`` 工具规格——启停自动观察（幂等命令）"""
    return ToolSpec(
        name="set_auto",
        description=(
            "启停文字冒险游戏的自动观察：on=True 时游戏侧切到 auto 并由后台"
            "持续观察上报新屏；False 停止观察。重复设置同一状态不产生任何"
            "动作（幂等）。何时用：想让剧情自动推进、只在选项出现时被叫停"
            "就 set_auto(True)；想接管手动推进（advance/choose）就 set_auto"
            "(False)。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "on": {"type": "boolean", "description": "True=开启自动观察；False=停止"},
            },
            "required": ["on"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema=_SNAPSHOT_OUTPUT_SCHEMA,
    )


def build_get_state_spec() -> ToolSpec:
    """``text_adv_get_state`` 工具规格——状态快照只读查询（不触网不截图）"""
    return ToolSpec(
        name="get_state",
        description=(
            "查询文字冒险游戏当前状态快照：text（最近一屏正文）/ options"
            "（当前选项，index 为 1 起编号）/ auto（是否自动观察中）/ "
            "updated_at_ms。只读、不触网、不触发截图。何时用：当游戏叙事"
            "出现待定夺时用它看当前选项列表，再调 text_adv_choose(option=N)"
            " 告知选择；也用于回忆上一屏讲到哪。"
        ),
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema=_SNAPSHOT_OUTPUT_SCHEMA,
    )


def build_text_adv_visible_to() -> Dict[str, List[str]]:
    """四个工具的可见名单映射（注册处声明）：全部仅主播可见。"""
    return {
        spec.full_name: ["streamer"]
        for spec in (build_advance_spec(), build_choose_spec(), build_set_auto_spec(), build_get_state_spec())
    }


# ---------------------------------------------------------------------------
# Provider（注册到 ToolRegistry）
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TextAdvToolProvider(BaseToolProvider):
    """文字冒险 Agent 工具 Provider（provider="text_adv"）

    持有 Agent 主体与键鼠注入后端：动作（按键/点击）经 ``input_backend``
    触达真实游戏，感知与观察循环经 ``agent`` 承担；监视器原点解析经
    ``monitor_resolver``（默认查 mss 的 monitors 表，测试可注入假件）。

    注入模式（继承 + 构造注入）：
        >>> provider = TextAdvToolProvider(agent=agent, input_backend=backend)
        >>> registry.register_provider(provider, visible_to=build_text_adv_visible_to())
    """

    agent: TextAdvGameAgent
    input_backend: InputBackend
    monitor_resolver: MonitorResolver = field(default=_query_monitor_geometry)

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category: ClassVar[str] = "game"

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def list_tools(self) -> Iterable[ToolSpec]:
        return [build_advance_spec(), build_choose_spec(), build_set_auto_spec(), build_get_state_spec()]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        name = invocation.tool_name
        args: Dict[str, Any] = invocation.arguments or {}
        started_ms = int(time.time() * 1000)

        try:
            # 调用方使用的就是派生全名（text_adv_advance 等），等值对照分发
            if name == "text_adv_advance":
                return await self._invoke_advance(started_ms)
            if name == "text_adv_choose":
                return await self._invoke_choose(args, started_ms)
            if name == "text_adv_set_auto":
                return await self._invoke_set_auto(args, started_ms)
            if name == "text_adv_get_state":
                return self._snapshot_result("text_adv_get_state", started_ms)
            return self._fail(name, started_ms, f"未知 TextAdv 工具 '{name}'")
        except Exception as exc:  # noqa: BLE001 - 工具边界兜底，异常转失败结果
            logger.exception(f"TextAdv 工具 '{name}' 执行异常: {type(exc).__name__}: {exc}")
            return self._fail(name, started_ms, f"{type(exc).__name__}: {exc}")

    # ==================================================================
    # text_adv_advance：按键推进 → 等稳定 → 识别 → 快照
    # ==================================================================

    async def _invoke_advance(self, started_ms: int) -> ToolExecutionResult:
        cfg = self.agent.typed_config
        ok, reason = self._guard_window()
        if not ok:
            return self._fail("text_adv_advance", started_ms, reason or "窗口护栏未通过")

        key = cfg.keys.get("advance", "space")
        try:
            self.input_backend.press(key)
        except Exception as exc:  # noqa: BLE001 - 注入失败转失败结果
            logger.exception(f"推进按键失败（key={key}）: {type(exc).__name__}: {exc}")
            return self._fail("text_adv_advance", started_ms, f"推进按键失败: {type(exc).__name__}: {exc}")

        stable = await self._stable_frame()
        reading = await self._read_screen()
        if stable is None or not stable.image or reading is None or not reading.parse_ok:
            detail = (
                "画面采集失败"
                if stable is None or not stable.image
                else (reading.parse_error if reading is not None else "读屏调用失败")
            )
            return self._capture_failed_result("text_adv_advance", started_ms, detail)

        self.agent._game_state.record_screen(reading.text, reading.options)  # noqa: SLF001 - 同包感知状态
        return self._snapshot_result("text_adv_advance", started_ms, content=reading.text)

    # ==================================================================
    # text_adv_choose：重读 → 校验 → 点击 → 验证 → 快照
    # ==================================================================

    async def _invoke_choose(self, args: Dict[str, Any], started_ms: int) -> ToolExecutionResult:
        raw_option: object = args.get("option")
        if not isinstance(raw_option, (int, str)):
            return self._fail("text_adv_choose", started_ms, f"option 必须是整数序号，得到 {raw_option!r}")
        try:
            option = int(raw_option)
        except ValueError:
            return self._fail("text_adv_choose", started_ms, f"option 必须是整数序号，得到 {raw_option!r}")

        ok, reason = self._guard_window()
        if not ok:
            return self._fail("text_adv_choose", started_ms, reason or "窗口护栏未通过")

        # 每次调用先重读当前屏：不维护跨屏脏表，所见即所点
        reading = await self._read_screen()
        if reading is None or not reading.parse_ok:
            detail = reading.parse_error if reading is not None else "读屏调用失败"
            return self._fail("text_adv_choose", started_ms, f"重读当前屏失败，放弃选择：{detail}")
        if not reading.options:
            return self._fail("text_adv_choose", started_ms, "当前屏无选项，无法选择")
        if not 1 <= option <= len(reading.options):
            return self._fail(
                "text_adv_choose",
                started_ms,
                f"序号 {option} 不存在：当前共 {len(reading.options)} 项",
            )
        chosen = reading.options[option - 1]
        if not chosen.clickable or chosen.vlm_xy is None:
            return self._fail(
                "text_adv_choose",
                started_ms,
                f"选项 {option}（{chosen.label}）不可点：识别未给出可用坐标",
            )

        geometry = self.monitor_resolver(self.agent.typed_config.monitor_index)
        if geometry is None:
            return self._fail("text_adv_choose", started_ms, "监视器原点查询失败，坐标链路不可用")
        coords_region = self._coords_region(geometry)
        if reading.sent_width <= 0:
            return self._fail("text_adv_choose", started_ms, "识别图像宽度未知，坐标链路不可用")
        abs_xy = vlm_xy_to_screen(chosen.vlm_xy, reading.sent_width, coords_region, (geometry.left, geometry.top))
        if abs_xy is None:
            return self._fail(
                "text_adv_choose",
                started_ms,
                f"选项 {option} 的坐标反算越界（vlm_xy={chosen.vlm_xy}），拒绝点击",
            )

        try:
            self.input_backend.click(abs_xy[0], abs_xy[1])
        except Exception as exc:  # noqa: BLE001 - 注入失败转失败结果
            logger.exception(f"选项点击失败（{abs_xy}）: {type(exc).__name__}: {exc}")
            return self._fail("text_adv_choose", started_ms, f"选项点击失败: {type(exc).__name__}: {exc}")

        # 点击后验证：等稳定后重读，选项屏应消失或正文文本变化；不过则不二次点击
        stable = await self._stable_frame()
        verified = await self._read_screen()
        changed = (
            stable is not None
            and stable.image
            and verified is not None
            and verified.parse_ok
            and (not verified.options or text_key(verified.text) != text_key(reading.text))
        )
        if not changed:
            return self._fail(
                "text_adv_choose",
                started_ms,
                "点击后画面未变化，选项可能未生效；为避免误触不重试，请重试前先用 text_adv_get_state 确认当前屏",
            )

        self.agent._game_state.record_screen(verified.text, verified.options)  # noqa: SLF001 - 同包感知状态
        return self._snapshot_result("text_adv_choose", started_ms, content=verified.text)

    # ==================================================================
    # text_adv_set_auto：幂等翻标志（必要经 AUTO 按钮点击）
    # ==================================================================

    async def _invoke_set_auto(self, args: Dict[str, Any], started_ms: int) -> ToolExecutionResult:
        on = bool(args.get("on"))

        # 幂等：目标状态与当前一致时不点按钮——AUTO 是切换按钮，二次点击
        # 会把游戏 auto 又切回去
        if self.agent.auto == on:
            return self._snapshot_result("text_adv_set_auto", started_ms)

        notice: Optional[str] = None
        button_xy = self.agent.typed_config.auto_button_xy
        if button_xy is not None:
            ok, reason = self._guard_window()
            if not ok:
                return self._fail("text_adv_set_auto", started_ms, reason or "窗口护栏未通过")
            geometry = self.monitor_resolver(self.agent.typed_config.monitor_index)
            if geometry is not None:
                abs_xy = (geometry.left + button_xy[0], geometry.top + button_xy[1])
            else:
                # 原点不可得时按显示器原点为 (0,0) 兜底（单显示器布局通常成立）
                logger.warning("监视器原点查询失败，AUTO 按钮坐标按 (0,0) 原点使用")
                abs_xy = (button_xy[0], button_xy[1])
            try:
                self.input_backend.click(abs_xy[0], abs_xy[1])
            except Exception as exc:  # noqa: BLE001 - 点击失败不翻标志
                logger.exception(f"AUTO 按钮点击失败（{abs_xy}）: {type(exc).__name__}: {exc}")
                return self._fail("text_adv_set_auto", started_ms, f"AUTO 按钮点击失败: {type(exc).__name__}: {exc}")
        else:
            notice = "未标定 AUTO 按钮坐标，仅切换观察循环"

        # 循环由 Agent 自持：工具只翻标志，绝不自己拉起循环
        await self.agent.set_auto(on)
        return self._snapshot_result("text_adv_set_auto", started_ms, notice=notice)

    # ==================================================================
    # 内部辅助
    # ==================================================================

    def _guard_window(self) -> Tuple[bool, Optional[str]]:
        """窗口护栏：查找游戏窗口并真夺焦（advance 是显式命令，允许夺焦）。

        Returns:
            ``(通过, 失败原因)``；通过时原因为 ``None``。
        """
        window_backend = self.agent._window_backend  # noqa: SLF001 - 同包感知依赖
        keyword = self.agent.typed_config.game_window_title_keyword
        win: Optional[WindowInfo] = window_backend.find(keyword)
        if win is None:
            return False, f"未找到游戏窗口（标题关键词={keyword!r}）"
        if not window_backend.focus(win):
            return False, f"游戏窗口夺焦失败（title={win.title!r}）"
        return True, None

    async def _stable_frame(self) -> Optional[StableFrameResult]:
        """等待画面稳定；采集异常按感知失败降级（返回 ``None``，不外抛）。"""
        cfg = self.agent.typed_config
        try:
            return await wait_stable(
                self.agent._capture,  # noqa: SLF001 - 同包感知依赖
                self._capture_region(),
                cfg.monitor_index,
                sample_ms=cfg.stability_sample_ms,
                consecutive=cfg.stability_consecutive,
                timeout_ms=cfg.stability_timeout_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 采集异常是感知失败，不是工具异常
            logger.warning(f"稳定判定期间采集异常: {type(exc).__name__}: {exc}")
            return None

    async def _read_screen(self) -> Optional[ScreenReading]:
        """读一次当前屏；读屏调用异常按感知失败降级（返回 ``None``，不外抛）。"""
        try:
            return await self.agent._vision_reader.read_screen(want_options=True)  # noqa: SLF001 - 同包感知依赖
        except Exception as exc:  # noqa: BLE001 - 读屏异常是感知失败，不是工具异常
            logger.warning(f"读屏调用异常: {type(exc).__name__}: {exc}")
            return None

    def _capture_region(self) -> Optional[Tuple[int, int, int, int]]:
        """配置区域 [x, y, w, h] → 采集契约的 (x1, y1, x2, y2)；未配置返回 None。"""
        raw = self.agent.typed_config.region
        if raw is None or len(raw) != 4:
            return None
        x, y, w, h = raw
        return (x, y, x + w, y + h)

    def _coords_region(self, geometry: MonitorGeometry) -> Tuple[int, int, int, int]:
        """坐标反算用的 region (left, top, width, height)；未配置时取整台显示器。"""
        raw = self.agent.typed_config.region
        if raw is None or len(raw) != 4:
            return (0, 0, geometry.width, geometry.height)
        return (raw[0], raw[1], raw[2], raw[3])

    def _snapshot_result(
        self,
        tool_name: str,
        started_ms: int,
        *,
        content: str = "",
        notice: Optional[str] = None,
    ) -> ToolExecutionResult:
        """以 Agent 当前状态快照构造成功结果（键集由 state.to_dict 唯一定义）。"""
        snapshot = self.agent.get_state_snapshot()
        structured: Dict[str, Any] = dict(snapshot)
        if notice is not None:
            structured["notice"] = notice
        finished_ms = int(time.time() * 1000)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content=content if content else str(snapshot.get("text") or ""),
            structured_content=structured,
            timestamp_ms=finished_ms,
            duration_ms=finished_ms - started_ms,
        )

    def _capture_failed_result(self, tool_name: str, started_ms: int, detail: Optional[str]) -> ToolExecutionResult:
        """感知失败的成功形态：``success=True`` + 空 text + 结构化 error，不发事件。"""
        reason = detail or "未知原因"
        logger.warning(f"{tool_name} 感知失败: {reason}")
        finished_ms = int(time.time() * 1000)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content="",
            structured_content={"error": f"capture_failed: {reason}"},
            timestamp_ms=finished_ms,
            duration_ms=finished_ms - started_ms,
        )

    def _fail(self, tool_name: str, started_ms: int, reason: str) -> ToolExecutionResult:
        """失败结果：原因进 ``error_message`` 与结构化 ``error``，不发事件。"""
        finished_ms = int(time.time() * 1000)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error_message=reason,
            structured_content={"error": reason},
            timestamp_ms=finished_ms,
            duration_ms=finished_ms - started_ms,
        )


__all__ = [
    "PROVIDER_NAME",
    "MonitorGeometry",
    "TextAdvToolProvider",
    "build_advance_spec",
    "build_choose_spec",
    "build_set_auto_spec",
    "build_get_state_spec",
    "build_text_adv_visible_to",
]
