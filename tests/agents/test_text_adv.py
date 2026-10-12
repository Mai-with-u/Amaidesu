"""TextAdvGameAgent 测试（观察循环 + auto 标志 + 事件分流）

覆盖：
- 元数据：name / emits_events（game.milestone / game.report / game.error）
- set_auto 幂等：连调两次只产生一个观察循环 Task
- set_auto(False)：Task 取消、auto 标志清零
- 错误分流：感知失败只记日志不发 game.error、循环存活
- 循环整体死亡：game.error 恰好一条 + auto 回落 False
- 窗口护栏：失焦停循环 + 恰好一条 game.error（连跑多轮不刷屏）
- 首屏上报：启动后读一次当前屏（有选项 → report；无选项 → milestone）
- 文本去重：同屏文本不重复上报；连续无新屏后暂停 VLM 读取
- 屏幕线既有契约：look_at_screen capture 失败 → success=True + 结构化 error（原样保留）
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Dict, List, Optional, Tuple

from pytest import MonkeyPatch

from src.agents.text_adv import (
    MonitorGeometry,
    TextAdvConfig,
    TextAdvGameAgent,
    TextAdvToolProvider,
    build_text_adv_agent,
    build_text_adv_visible_to,
)
from src.agents.text_adv.input import FakeInputBackend
from src.agents.text_adv.vlm import FakeVisionReader, ScreenReading
from src.agents.text_adv.window import FakeWindowBackend
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.vision import LookAtScreenProvider
from src.modules.vision.look_at_screen import ScreenCaptureResult


# =============================================================================
# 假件与辅助
# =============================================================================


class StaticCapture:
    """每次返回同一帧的假采集后端（快速达成稳定判定）。"""

    def __init__(self, frame: bytes = b"frame-a") -> None:
        self.frame = frame
        self.calls = 0

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        self.calls += 1
        return ScreenCaptureResult(image=self.frame)


class GenCapture:
    """每次返回互不相同帧的假采集后端（模拟画面持续变化，帧级去重永不命中）。"""

    def __init__(self) -> None:
        self.calls = 0

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        self.calls += 1
        return ScreenCaptureResult(image=f"gen-{self.calls}".encode("utf-8"))


class BoomCapture:
    """capture 抛异常的假采集后端（模拟采集持续失败）。"""

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        raise RuntimeError("screen unavailable")


class BoomReader:
    """read_screen 抛异常的假读屏后端（驱动循环整体死亡路径）。"""

    async def read_screen(self, *, want_options: bool) -> ScreenReading:
        raise RuntimeError("reader boom")


# 契约格式回复：带选项屏
REPLY_WITH_OPTIONS = "正文：\n（村口）你面前出现两条路。\n选项：\n1. 继续前进 (100, 200)\n2. 回头看看 (300, 200)"
# 契约格式回复：纯叙事屏
REPLY_PLAIN = "正文：\n风静静地吹着。"


def fast_config() -> TextAdvConfig:
    """测试用快节奏配置（毫秒级采样/超时，小去重上限）。"""
    return TextAdvConfig(
        stability_sample_ms=10,
        stability_consecutive=2,
        stability_timeout_ms=120,
        no_change_limit=3,
    )


async def wait_for(condition: Callable[[], bool], timeout: float = 2.0) -> bool:
    """轮询条件成立；超时返回条件最终取值（不抛）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return condition()


def make_agent(
    *,
    reader: object | None = None,
    window: FakeWindowBackend | None = None,
    capture: object | None = None,
    config: TextAdvConfig | None = None,
) -> Tuple[TextAdvGameAgent, Dict[str, List[GamePayload]]]:
    """构造注入全假件的 Agent + 真实 EventBus 的事件收集器。"""
    bus = EventBus()
    collected: Dict[str, List[GamePayload]] = {"milestone": [], "report": [], "error": []}

    def _make(kind: str, key: str) -> Callable[[str, GamePayload, str], None]:
        async def _on(event_name: str, payload: GamePayload, source: str) -> None:
            collected[key].append(payload)

        return _on  # type: ignore[return-value]

    bus.on(CoreEvents.GAME_MILESTONE, _make("milestone", "milestone"), model_class=GamePayload)
    bus.on(CoreEvents.GAME_REPORT, _make("report", "report"), model_class=GamePayload)
    bus.on(CoreEvents.GAME_ERROR, _make("error", "error"), model_class=GamePayload)

    agent = TextAdvGameAgent(
        config or fast_config(),
        vision_reader=reader or FakeVisionReader(REPLY_PLAIN),  # type: ignore[arg-type]
        window_backend=window or FakeWindowBackend(),
        capture=capture or StaticCapture(),  # type: ignore[arg-type]
        input_backend=FakeInputBackend(),
        event_bus=bus,
    )
    return agent, collected


# =============================================================================
# 元数据
# =============================================================================


def test_text_adv_agent_metadata() -> None:
    """name 与描述。"""
    assert TextAdvGameAgent.name == "text_adv"
    assert "文字冒险" in TextAdvGameAgent.description


def test_text_adv_agent_emits_game_events() -> None:
    """事件族声明：milestone / report / error 三个 game.* 事件。"""
    assert CoreEvents.GAME_MILESTONE in TextAdvGameAgent.emits_events
    assert CoreEvents.GAME_REPORT in TextAdvGameAgent.emits_events
    assert CoreEvents.GAME_ERROR in TextAdvGameAgent.emits_events


def test_build_text_adv_agent_returns_agent() -> None:
    """便捷构造函数：依赖显式传参，返回未启动实例。"""
    agent = build_text_adv_agent(
        config=fast_config(),
        vision_reader=FakeVisionReader(REPLY_PLAIN),
        window_backend=FakeWindowBackend(),
        capture=StaticCapture(),
        input_backend=FakeInputBackend(),
    )
    assert isinstance(agent, TextAdvGameAgent)
    assert agent.name == "text_adv"
    assert agent.auto is False


def test_factory_enables_tools_for_streamer_and_stop_removes(monkeypatch: MonkeyPatch) -> None:
    """工厂装配接线：注册进 registry 的 Agent 启动后主播可见四工具；stop 后摘除。"""
    from src.modules.agents.factory import instantiate_agent

    monkeypatch.setattr("src.agents.text_adv.input.PyAutoGuiInputBackend", FakeInputBackend)

    registry = ToolRegistry()
    agent = instantiate_agent(
        "text_adv",
        {"stability_sample_ms": 10, "stability_consecutive": 2, "stability_timeout_ms": 120},
        llm_manager=object(),
        prompt_manager=object(),
        tool_registry=registry,
    )
    assert agent is not None

    async def _run() -> list[str]:
        await agent.start()
        names = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
        await agent.stop()
        return sorted(names)  # type: ignore[arg-type]

    after_start = asyncio.run(_run())
    assert after_start == ["text_adv_advance", "text_adv_choose", "text_adv_get_state", "text_adv_set_auto"]
    after_stop = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
    assert not any(name.startswith("text_adv_") for name in after_stop)


# =============================================================================
# set_auto：幂等启停
# =============================================================================


async def test_set_auto_true_is_idempotent() -> None:
    """连调两次 set_auto(True)：只产生一个观察循环 Task。"""
    agent, _ = make_agent()
    await agent.set_auto(True)
    task_first = agent._observe_task
    assert task_first is not None
    await asyncio.sleep(0.05)
    await agent.set_auto(True)
    assert agent._observe_task is task_first, "重复 set_auto(True) 不应创建新 Task"
    assert not task_first.done()
    assert agent.auto is True
    await agent.set_auto(False)


async def test_set_auto_false_cancels_loop() -> None:
    """set_auto(False)：观察 Task 被取消、auto 标志回落 False。"""
    agent, _ = make_agent()
    await agent.set_auto(True)
    task = agent._observe_task
    assert task is not None
    await asyncio.sleep(0.05)
    await agent.set_auto(False)
    assert agent.auto is False
    assert task.done() or task.cancelled()


# =============================================================================
# 首屏上报与去重
# =============================================================================


async def test_first_screen_with_options_emits_report_once() -> None:
    """首屏带选项：启动后恰好一条 game.report（escalation），message 含选项与工具提示。"""
    agent, collected = make_agent(reader=FakeVisionReader(REPLY_WITH_OPTIONS))
    await agent.set_auto(True)
    ok = await wait_for(lambda: len(collected["report"]) >= 1)
    assert ok, "首屏 report 未上报"
    await asyncio.sleep(0.1)
    assert len(collected["report"]) == 1
    assert len(collected["milestone"]) == 0
    payload = collected["report"][0]
    assert payload.report_kind == "escalation"
    assert "text_adv_choose" in payload.message
    assert "1. 继续前进" in payload.message
    await agent.set_auto(False)


async def test_first_screen_plain_emits_milestone_once() -> None:
    """首屏无选项：启动后恰好一条 game.milestone。"""
    agent, collected = make_agent(reader=FakeVisionReader(REPLY_PLAIN))
    await agent.set_auto(True)
    ok = await wait_for(lambda: len(collected["milestone"]) >= 1)
    assert ok, "首屏 milestone 未上报"
    await asyncio.sleep(0.1)
    assert len(collected["milestone"]) == 1
    assert len(collected["report"]) == 0
    assert "风静静地吹着" in collected["milestone"][0].message
    await agent.set_auto(False)


async def test_same_text_not_reported_twice_and_vlm_pauses() -> None:
    """帧持续变化但文本同屏：不重复上报；连续无新屏达上限后暂停 VLM 读取。"""
    reader = FakeVisionReader(REPLY_PLAIN)
    agent, collected = make_agent(reader=reader, capture=GenCapture())
    await agent.set_auto(True)
    ok = await wait_for(lambda: len(collected["milestone"]) >= 1)
    assert ok
    # 首屏之后再跑若干轮：文本无新 → 不重复上报；no_change_limit(=3) 达到后暂停 VLM
    # （读屏序列：首屏 1 次 + 无新计数 3 次，之后帧级检查，读屏次数定格在 4）
    ok = await wait_for(lambda: len(reader.calls) >= 4, timeout=3.0)
    assert ok, f"帧变化应触发多次读屏，实际 calls={len(reader.calls)}"
    await asyncio.sleep(0.1)
    assert len(collected["milestone"]) == 1, "同屏文本不得重复上报"
    calls_at_pause = len(reader.calls)
    await asyncio.sleep(0.3)  # 足够跑两轮以上，验证读屏已停
    assert len(reader.calls) == calls_at_pause, "暂停后不应继续消耗 VLM"
    assert len(collected["milestone"]) == 1
    await agent.set_auto(False)


# =============================================================================
# 错误分流
# =============================================================================


async def test_perception_failure_quiet_loop_survives() -> None:
    """感知失败（capture 抛异常）：无 game.error，循环仍存活继续后续轮次。"""
    agent, collected = make_agent(capture=BoomCapture())
    await agent.set_auto(True)
    await asyncio.sleep(0.4)
    assert collected["error"] == [], "感知失败不得发 game.error"
    assert agent.auto is True
    task = agent._observe_task
    assert task is not None and not task.done(), "感知失败后循环应继续存活"
    await agent.set_auto(False)


async def test_loop_death_emits_error_once_and_auto_falls_back() -> None:
    """循环整体死亡：game.error 恰好一条 + auto 回落 False。"""
    agent, collected = make_agent(reader=BoomReader())
    await agent.set_auto(True)
    ok = await wait_for(lambda: len(collected["error"]) >= 1)
    assert ok, "循环死亡应发 game.error"
    await asyncio.sleep(0.1)
    assert len(collected["error"]) == 1
    assert agent.auto is False
    task = agent._observe_task
    assert task is not None and task.done(), "循环应已退出"
    await agent.set_auto(False)


async def test_focus_loss_stops_loop_with_single_error() -> None:
    """窗口失焦：停循环 + 恰好一条 game.error（连跑多轮不刷屏）。"""
    window = FakeWindowBackend()
    agent, collected = make_agent(window=window)
    await agent.set_auto(True)
    ok = await wait_for(lambda: len(collected["milestone"]) >= 1)
    assert ok, "失焦前首屏应已上报"
    window.set_foreground(False)
    ok = await wait_for(lambda: len(collected["error"]) >= 1)
    assert ok, "失焦应触发一条 game.error"
    await asyncio.sleep(0.3)  # 足够跑多轮，验证不刷屏
    assert len(collected["error"]) == 1, "护栏失败只报一次"
    assert agent.auto is False
    task = agent._observe_task
    assert task is not None and task.done(), "失焦后循环应退出"
    await agent.set_auto(False)


async def test_window_not_found_stops_with_single_error() -> None:
    """窗口未找到：停循环 + 恰好一条 game.error。"""
    window = FakeWindowBackend(found=False)
    agent, collected = make_agent(window=window)
    await agent.set_auto(True)
    ok = await wait_for(lambda: len(collected["error"]) >= 1)
    assert ok
    await asyncio.sleep(0.1)
    assert len(collected["error"]) == 1
    assert agent.auto is False
    await agent.set_auto(False)


# =============================================================================
# 生命周期
# =============================================================================


async def test_on_stop_cancels_observe_loop() -> None:
    """_on_stop：取消观察循环。"""
    agent, _ = make_agent()
    await agent.start()
    await agent.set_auto(True)
    task = agent._observe_task
    assert task is not None
    await agent.stop()
    assert task.done() or task.cancelled()
    assert agent.auto is False


async def test_state_snapshot_carries_auto_flag() -> None:
    """状态快照键集恰为 {text, options, auto, updated_at_ms}，auto 由 Agent 注入。"""
    agent, _ = make_agent()
    snap = agent.get_state_snapshot()
    assert set(snap.keys()) == {"text", "options", "auto", "updated_at_ms"}
    assert snap["auto"] is False


# =============================================================================
# 屏幕线既有契约（原样保留：感知失败不外抛、不发业务事件）
# =============================================================================


async def test_look_at_screen_capture_failure_has_error_in_structured() -> None:
    """契约直接断言：look_at_screen capture 抛异常 → success=True + error 字段含 'capture_failed'。

    屏幕感知重推后的契约：capture 异常 / 空图 → 工具内降级为 ``success=True`` +
    ``text=""`` + ``structured_content["error"]="capture_failed: ..."``，调用方据此继续；
    不发 ``game.error`` 事件（新契约：感知失败不外抛、不发业务事件，错误信号在结构化内容里）。
    """
    registry = ToolRegistry()

    provider = LookAtScreenProvider(config={}, screen_capture=BoomCapture())
    registry.register_provider(provider)

    res = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))
    assert res.success is True
    assert res.structured_content is not None
    assert "capture_failed" in str(res.structured_content.get("error") or "")
    assert res.structured_content.get("text") == ""


# =============================================================================
# 工具面（text_adv_advance / choose / set_auto / get_state）
# =============================================================================


def geometry_resolver(geom: MonitorGeometry | None) -> Callable[[int], MonitorGeometry | None]:
    """注入固定的监视器几何（None = 模拟查询失败）。"""

    def _resolve(monitor_index: int) -> MonitorGeometry | None:
        return geom

    return _resolve


def make_tools(
    *,
    reader: object | None = None,
    window: FakeWindowBackend | None = None,
    capture: object | None = None,
    config: TextAdvConfig | None = None,
    geom: MonitorGeometry | None = None,
) -> Tuple[TextAdvGameAgent, TextAdvToolProvider, FakeInputBackend, Dict[str, List[GamePayload]]]:
    """构造 Agent + 工具 Provider + 假键鼠后端 + 事件收集器（全假件，不触真机）。"""
    agent, collected = make_agent(reader=reader, window=window, capture=capture, config=config)
    input_backend = FakeInputBackend()
    provider = TextAdvToolProvider(
        agent=agent,
        input_backend=input_backend,
        monitor_resolver=geometry_resolver(geom if geom is not None else MonitorGeometry(0, 0, 1280, 720)),
    )
    return agent, provider, input_backend, collected


def make_registry(provider: TextAdvToolProvider) -> ToolRegistry:
    """按生产同构方式注册 provider（名单来自 build_text_adv_visible_to）。"""
    registry = ToolRegistry()
    registry.register_provider(provider, visible_to=build_text_adv_visible_to())
    return registry


def test_tools_visibility_streamer_only() -> None:
    """名单隔离：主播可见且仅可见这 4 个 text_adv_*；minecraft 一个都看不到。"""
    _agent, provider, _input, _collected = make_tools()
    registry = make_registry(provider)

    streamer_names = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
    assert streamer_names == {
        "text_adv_advance",
        "text_adv_choose",
        "text_adv_set_auto",
        "text_adv_get_state",
    }
    minecraft_names = {spec.full_name for spec in registry.list_tools(for_agent="minecraft")}
    assert not any(name.startswith("text_adv_") for name in minecraft_names)


async def test_get_state_exact_keys_and_no_capture() -> None:
    """get_state：键集精确为快照四键，且不触发截图、不消耗读屏。"""
    reader = FakeVisionReader(REPLY_PLAIN)
    capture = StaticCapture()
    _agent, provider, _input, _collected = make_tools(reader=reader, capture=capture)
    result = await provider.invoke(ToolInvocation(tool_name="text_adv_get_state", arguments={}, source="test"))
    assert result.success is True
    assert result.structured_content is not None
    assert set(result.structured_content.keys()) == {"text", "options", "auto", "updated_at_ms"}
    assert reader.calls == []
    assert capture.calls == 0


async def test_set_auto_clicks_button_once_and_is_idempotent() -> None:
    """set_auto(True)：标定坐标时点一次 AUTO 按钮；连调两次只点一次；auto 立即为 True。"""
    config = fast_config()
    config.auto_button_xy = (640, 680)
    agent, provider, input_backend, _collected = make_tools(config=config)
    result = await provider.invoke(ToolInvocation(tool_name="text_adv_set_auto", arguments={"on": True}, source="test"))
    assert result.success is True
    assert input_backend.calls.count("click:640,680,left") == 1
    assert agent.get_state_snapshot()["auto"] is True

    # 幂等：重复 True 不再点按钮（AUTO 是切换按钮，二次点击会关掉）
    result2 = await provider.invoke(
        ToolInvocation(tool_name="text_adv_set_auto", arguments={"on": True}, source="test")
    )
    assert result2.success is True
    assert input_backend.calls.count("click:640,680,left") == 1
    await agent.set_auto(False)


async def test_set_auto_uncalibrated_switches_flag_without_click() -> None:
    """未标定 AUTO 按钮：不点击、给结构化说明，仅切换观察循环标志。"""
    agent, provider, input_backend, _collected = make_tools()
    result = await provider.invoke(ToolInvocation(tool_name="text_adv_set_auto", arguments={"on": True}, source="test"))
    assert result.success is True
    assert input_backend.calls == []
    assert result.structured_content is not None
    assert "未标定" in str(result.structured_content.get("notice") or "")
    assert agent.get_state_snapshot()["auto"] is True
    await agent.set_auto(False)


async def test_choose_success_clicks_and_returns_new_snapshot() -> None:
    """choose 命中：点击反算出的绝对坐标，重读返回新屏快照，绝不自持循环。"""
    reader = FakeVisionReader(REPLY_PLAIN)
    reader.queue_reply(REPLY_WITH_OPTIONS)  # 首次重读：选项屏
    reader.queue_reply(REPLY_PLAIN)  # 点击后重读：选项屏消失
    agent, provider, input_backend, collected = make_tools(reader=reader)
    result = await provider.invoke(ToolInvocation(tool_name="text_adv_choose", arguments={"option": 1}, source="test"))
    assert result.success is True
    assert input_backend.calls == ["click:100,200,left"]
    assert result.structured_content is not None
    assert result.structured_content["text"] == "风静静地吹着。"
    assert collected["error"] == []
    assert agent.auto is False
    await agent.set_auto(False)


async def test_choose_rejections_no_options_and_invalid_index() -> None:
    """choose 拒绝：当前屏无选项 / 序号不存在——失败可读且零 game.error。"""
    agent, provider, input_backend, collected = make_tools(reader=FakeVisionReader(REPLY_PLAIN))

    result = await provider.invoke(ToolInvocation(tool_name="text_adv_choose", arguments={"option": 1}, source="test"))
    assert result.success is False
    assert "无选项" in (result.error_message or "")
    assert input_backend.calls == []

    reader = FakeVisionReader(REPLY_WITH_OPTIONS)
    _agent2, provider2, input2, collected2 = make_tools(reader=reader)
    result2 = await provider2.invoke(
        ToolInvocation(tool_name="text_adv_choose", arguments={"option": 3}, source="test")
    )
    assert result2.success is False
    assert "不存在" in (result2.error_message or "")
    assert input2.calls == []

    await asyncio.sleep(0.05)
    assert collected["error"] == []
    assert collected2["error"] == []


async def test_choose_rejection_not_clickable() -> None:
    """choose 拒绝：选项无坐标（不可点）——失败原因含"不可点"，零 game.error。"""
    reply = "正文：\n你面前出现两条路。\n选项：\n1. 继续前进\n2. 回头看看"
    _agent, provider, input_backend, collected = make_tools(reader=FakeVisionReader(reply))
    result = await provider.invoke(ToolInvocation(tool_name="text_adv_choose", arguments={"option": 1}, source="test"))
    assert result.success is False
    assert "不可点" in (result.error_message or "")
    assert input_backend.calls == []
    await asyncio.sleep(0.05)
    assert collected["error"] == []


async def test_choose_rejection_coords_out_of_bounds() -> None:
    """choose 拒绝：坐标反算越出显示器范围——失败原因含"越界"，零 game.error。"""
    config = fast_config()
    config.region = [900, 0, 320, 240]  # 区域偏移使反算结果越出显示器矩形
    _agent, provider, input_backend, collected = make_tools(reader=FakeVisionReader(REPLY_WITH_OPTIONS), config=config)
    result = await provider.invoke(ToolInvocation(tool_name="text_adv_choose", arguments={"option": 1}, source="test"))
    assert result.success is False
    assert "越界" in (result.error_message or "")
    assert input_backend.calls == []
    await asyncio.sleep(0.05)
    assert collected["error"] == []


async def test_choose_verify_failure_no_second_click() -> None:
    """点击后画面未变化：失败结果且只发生过一次点击（绝不二次盲点）。"""
    reader = FakeVisionReader(REPLY_WITH_OPTIONS)  # 固定回复：重读仍是同一选项屏
    _agent, provider, input_backend, collected = make_tools(reader=reader)
    result = await provider.invoke(ToolInvocation(tool_name="text_adv_choose", arguments={"option": 1}, source="test"))
    assert result.success is False
    assert "画面未变化" in (result.error_message or "")
    assert len([c for c in input_backend.calls if c.startswith("click:")]) == 1
    await asyncio.sleep(0.05)
    assert collected["error"] == []


async def test_advance_success_presses_and_records_screen() -> None:
    """advance 成功：按下推进键、记录新屏并返回快照（键集与快照一致、零事件）。"""
    reader = FakeVisionReader(REPLY_PLAIN)
    agent, provider, input_backend, collected = make_tools(reader=reader)
    result = await provider.invoke(ToolInvocation(tool_name="text_adv_advance", arguments={}, source="test"))
    assert result.success is True
    assert "press:space" in input_backend.calls
    assert result.content == "风静静地吹着。"
    assert result.structured_content is not None
    assert set(result.structured_content.keys()) == {"text", "options", "auto", "updated_at_ms"}
    assert result.structured_content["text"] == "风静静地吹着。"
    assert agent.get_state_snapshot()["text"] == "风静静地吹着。"
    await asyncio.sleep(0.05)
    assert collected["error"] == []
    assert collected["milestone"] == []
    assert collected["report"] == []


async def test_advance_rejection_window_guard_fails() -> None:
    """advance 拒绝：窗口护栏未通过（窗口未找到）——失败可读、未按键、零事件。"""
    agent, provider, input_backend, collected = make_tools(window=FakeWindowBackend(found=False))
    result = await provider.invoke(ToolInvocation(tool_name="text_adv_advance", arguments={}, source="test"))
    assert result.success is False
    assert "未找到游戏窗口" in (result.error_message or "")
    assert input_backend.calls == []
    await asyncio.sleep(0.05)
    assert collected["error"] == []


async def test_advance_perception_failure_quiet_capture_failed() -> None:
    """advance 感知失败（读屏异常）：success=True + 结构化 capture_failed，零事件。"""
    agent, provider, input_backend, collected = make_tools(reader=BoomReader())
    result = await provider.invoke(ToolInvocation(tool_name="text_adv_advance", arguments={}, source="test"))
    assert result.success is True
    assert result.content == ""
    assert result.structured_content is not None
    assert "capture_failed" in str(result.structured_content.get("error") or "")
    assert "press:space" in input_backend.calls
    await asyncio.sleep(0.05)
    assert collected["error"] == []
    assert collected["milestone"] == []
