"""文字冒险 Agent 工具面（text_adv_advance / choose / set_auto / get_state）

主播与游戏之间的全部接口，名单全部 ``["streamer"]``（各 spec 的
``visible_to`` 字段声明）。

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
- 采集语义（窗口护栏/稳定帧/读屏/区域换算）经 ``agent.session`` 的
  :class:`ScreenSession` 单点承担——会话管采集、本类管处置
- 观察循环由 Agent 自持（经 ``agent.set_auto`` 翻标志拉起/停止），工具
  绝不自持循环

对外全名由 ``ToolSpec.full_name`` 派生（``text_adv_<工具名>``），
工具名里不手写前缀。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Protocol

from src.modules.agents.game_tools import (
    GameToolProvider,
    capture_failed_result,
    fail_result,
    snapshot_result,
    spec_named_handlers,
)
from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolSpec,
)

from .config import TextAdvConfig
from .coords import vlm_xy_to_screen
from .input import InputBackend
from .screen import text_key
from .session import MonitorResolver, ScreenSession, query_monitor_geometry
from .vlm import Option, ScreenReading

logger = get_logger("TextAdvTools")

# 提供者标识统一来源（ToolSpec.provider / 追溯用），避免字面量重复
PROVIDER_NAME = "text_adv"

# 工具面使用说明（给主播的玩法）：文字冒险是遥控型游戏——推进与选择都由主播直接
# 用工具做，观察循环只负责把新剧情与待选项上报；没有委派，也没有任务号。
STREAMER_GUIDE = (
    "文字冒险（视觉小说）是遥控型游戏：剧情由主播自己推进、选项由她自己点——"
    "想让剧情自动走就 text_adv_set_auto(on=true)，之后新剧情会以游戏记录送来；"
    "画面出现选项时游戏记录会写明「请调用 text_adv_choose(option=N)」，按主播人设定夺后直接调它；"
    "想手动逐屏看就 text_adv_set_auto(on=false) 再用 text_adv_advance，随时用 text_adv_get_state 看当前屏。"
    "它没有后台任务：不要对它用 framework_delegate 或 framework_prompt。"
)


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
        visible_to=["streamer"],
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
        visible_to=["streamer"],
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
        visible_to=["streamer"],
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
        visible_to=["streamer"],
        output_schema=_SNAPSHOT_OUTPUT_SCHEMA,
    )


# ---------------------------------------------------------------------------
# Provider（注册到 ToolRegistry）
# ---------------------------------------------------------------------------


class TextAdvToolHost(Protocol):
    """工具面对 Agent 的依赖切片（领域面）：观察会话 + 运行配置 + auto 命令 + 状态出口。

    工具层只依赖本协议声明的成员（采集经会话、动作出口经键鼠后端、
    观察循环启停只经 ``set_auto``、快照与识别记录经两个出口方法），不
    感知 Agent 的其余接口；Agent 本体结构性满足本协议，两模块互不
    import（依赖方向：agent → tools 单向）。
    """

    session: ScreenSession
    typed_config: TextAdvConfig

    @property
    def auto(self) -> bool:
        """当前是否处于自动观察模式。"""
        ...

    async def set_auto(self, on: bool) -> None:
        """启停自动观察（观察循环由 Agent 自持，工具只翻标志）。"""
        ...

    def get_state_snapshot(self) -> Dict[str, Any]:
        """导出状态快照（工具返回的 structured_content 键集来源）。"""
        ...

    def record_screen(self, text: str, options: Optional[List[Option]] = None) -> None:
        """记录一次识别结果到游戏状态。"""
        ...


@dataclass(slots=True)
class TextAdvToolProvider(GameToolProvider):
    """文字冒险 Agent 工具 Provider（provider="text_adv"）

    依赖收窄到 :class:`TextAdvToolHost` 切片与键鼠注入后端：动作（按键/
    点击）经 ``input_backend`` 触达真实游戏；感知（窗口护栏/稳定帧/读屏/
    区域换算）经宿主的 :class:`ScreenSession` 承担（会话管采集、本类管
    处置——护栏失败回执错误、感知失败降级为 capture_failed 回执）；监视
    器原点解析经 ``monitor_resolver``（默认查 mss 的 monitors 表，测试可
    注入假件）。分发、计时与异常转结果走共享管道（:class:`GameToolProvider`）。

    注入模式（继承 + 构造注入）：
        >>> provider = TextAdvToolProvider(agent=agent, input_backend=backend)
        >>> registry.register_provider(provider)
    """

    agent: TextAdvToolHost
    input_backend: InputBackend
    monitor_resolver: MonitorResolver = field(default=query_monitor_geometry)

    # 日志前缀（共享管道的异常日志区分用）
    log_label: ClassVar[str] = "TextAdv"

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def list_tools(self) -> Iterable[ToolSpec]:
        return [build_advance_spec(), build_choose_spec(), build_set_auto_spec(), build_get_state_spec()]

    def guide_for(self, agent_name: str) -> str:
        """玩法说明：四个工具只对主播可见，读者固定是主播，不按读者区分。"""
        del agent_name
        return STREAMER_GUIDE

    def tool_handlers(self) -> Dict[str, Any]:
        """对外全名 → 处理器（键从 spec 派生，不手拼前缀）。"""
        return spec_named_handlers(
            [
                (build_advance_spec(), self._invoke_advance),
                (build_choose_spec(), self._invoke_choose),
                (build_set_auto_spec(), self._invoke_set_auto),
                (build_get_state_spec(), self._invoke_get_state),
            ]
        )

    # ==================================================================
    # text_adv_advance：按键推进 → 等稳定 → 识别 → 快照
    # ==================================================================

    async def _inject(self, action: Callable[[], None]) -> None:
        """在 worker 线程执行键鼠注入：注入 API（SendInput/sleep）为同步调用，
        事件循环内直接调用会按注入时长冻结整个进程（hold 按住多久停摆多久）。"""
        await asyncio.to_thread(action)

    async def _invoke_advance(self, args: Dict[str, Any], started_ms: int) -> ToolExecutionResult:
        del args
        cfg = self.agent.typed_config
        refusal = self._refuse_without_window("text_adv_advance", started_ms)
        if refusal is not None:
            return refusal

        key = cfg.keys.get("advance", "space")
        try:
            await self._inject(lambda: self.input_backend.press(key))
        except Exception as exc:  # noqa: BLE001 - 注入失败转失败结果
            logger.exception(f"推进按键失败（key={key}）: {type(exc).__name__}: {exc}")
            return self._fail("text_adv_advance", started_ms, f"推进按键失败: {type(exc).__name__}: {exc}")

        stable = await self.agent.session.stable_frame()
        reading = await self._read_screen_quietly()
        if stable is None or not stable.image or reading is None or not reading.parse_ok:
            detail = (
                "画面采集失败"
                if stable is None or not stable.image
                else (reading.parse_error if reading is not None else "读屏调用失败")
            )
            return self._capture_failed_result("text_adv_advance", started_ms, detail)

        self.agent.record_screen(reading.text, reading.options)
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

        refusal = self._refuse_without_window("text_adv_choose", started_ms)
        if refusal is not None:
            return refusal

        # 每次调用先重读当前屏：不维护跨屏脏表，所见即所点
        reading = await self._read_screen_quietly()
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
        coords_region = self.agent.session.coords_region(geometry)
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
            await self._inject(lambda: self.input_backend.click(abs_xy[0], abs_xy[1]))
        except Exception as exc:  # noqa: BLE001 - 注入失败转失败结果
            logger.exception(f"选项点击失败（{abs_xy}）: {type(exc).__name__}: {exc}")
            return self._fail("text_adv_choose", started_ms, f"选项点击失败: {type(exc).__name__}: {exc}")

        # 点击后验证：等稳定后重读，选项屏应消失或正文文本变化；不过则不二次点击
        stable = await self.agent.session.stable_frame()
        verified = await self._read_screen_quietly()
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

        self.agent.record_screen(verified.text, verified.options)
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
            refusal = self._refuse_without_window("text_adv_set_auto", started_ms)
            if refusal is not None:
                return refusal
            geometry = self.monitor_resolver(self.agent.typed_config.monitor_index)
            if geometry is not None:
                abs_xy = (geometry.left + button_xy[0], geometry.top + button_xy[1])
            else:
                # 原点不可得时按显示器原点为 (0,0) 兜底（单显示器布局通常成立）
                logger.warning("监视器原点查询失败，AUTO 按钮坐标按 (0,0) 原点使用")
                abs_xy = (button_xy[0], button_xy[1])
            try:
                await self._inject(lambda: self.input_backend.click(abs_xy[0], abs_xy[1]))
            except Exception as exc:  # noqa: BLE001 - 点击失败不翻标志
                logger.exception(f"AUTO 按钮点击失败（{abs_xy}）: {type(exc).__name__}: {exc}")
                return self._fail("text_adv_set_auto", started_ms, f"AUTO 按钮点击失败: {type(exc).__name__}: {exc}")
        else:
            notice = "未标定 AUTO 按钮坐标，仅切换观察循环"

        # 循环由 Agent 自持：工具只翻标志，绝不自己拉起循环
        await self.agent.set_auto(on)
        return self._snapshot_result("text_adv_set_auto", started_ms, notice=notice)

    async def _invoke_get_state(self, args: Dict[str, Any], started_ms: int) -> ToolExecutionResult:
        del args
        return self._snapshot_result("text_adv_get_state", started_ms)

    # ==================================================================
    # 内部辅助
    # ==================================================================

    def _refuse_without_window(self, tool_name: str, started_ms: int) -> Optional[ToolExecutionResult]:
        """窗口护栏（工具路径）：查找游戏窗口并真夺焦（advance 是显式命令，允许夺焦）。

        处置归本类：护栏未通过即回执失败结果（观察循环的同一失败则停循环）。

        Returns:
            护栏未通过时为失败结果；通过时为 ``None``。
        """
        found = self.agent.session.find_window()
        if not found.ok or found.win is None:
            return self._fail(tool_name, started_ms, found.reason or "窗口护栏未通过")
        focus_reason = self.agent.session.focus_window(found.win)
        if focus_reason is not None:
            return self._fail(tool_name, started_ms, focus_reason)
        return None

    async def _read_screen_quietly(self) -> Optional[ScreenReading]:
        """读一次当前屏；读屏调用异常按感知失败降级（返回 ``None``，不外抛）。

        会话的 ``read_screen`` 透传后端异常（观察循环的处置是循环死亡），
        工具路径在此降级为可回执的感知失败。
        """
        try:
            return await self.agent.session.read_screen()
        except Exception as exc:  # noqa: BLE001 - 读屏异常是感知失败，不是工具异常
            logger.warning(f"读屏调用异常: {type(exc).__name__}: {exc}")
            return None

    def _snapshot_result(
        self,
        tool_name: str,
        started_ms: int,
        *,
        content: str = "",
        notice: Optional[str] = None,
    ) -> ToolExecutionResult:
        """以当前状态快照构造成功结果：content 缺省回落快照正文（构造细节见共享层）。"""
        snapshot = self.agent.get_state_snapshot()
        return snapshot_result(
            tool_name,
            started_ms,
            snapshot,
            content=content if content else str(snapshot.get("text") or ""),
            notice=notice,
        )

    def _capture_failed_result(self, tool_name: str, started_ms: int, detail: Optional[str]) -> ToolExecutionResult:
        """感知失败的成功形态（构造细节见共享层 ``capture_failed_result``）。"""
        return capture_failed_result(tool_name, started_ms, detail)

    def _fail(self, tool_name: str, started_ms: int, reason: str) -> ToolExecutionResult:
        """失败结果（构造细节见共享层 ``fail_result``）。"""
        return fail_result(tool_name, started_ms, reason)


__all__ = [
    "PROVIDER_NAME",
    "STREAMER_GUIDE",
    "TextAdvToolHost",
    "TextAdvToolProvider",
    "build_advance_spec",
    "build_choose_spec",
    "build_set_auto_spec",
    "build_get_state_spec",
]
