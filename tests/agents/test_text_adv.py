"""TextAdvGameAgent：观察循环与 auto 标志、事件分流（感知失败静默/循环死亡/窗口护栏各一条 game.error）、首屏与文本去重、四个工具

假件全套注入（窗口、采集、读屏、键鼠），真实 EventBus 收集 game.* 事件，不触真机。
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Dict, List, Optional, Tuple

import pytest

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
from src.modules.agents.deps import AgentDeps
from src.modules.agents.factory import instantiate_agent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.vision import LookAtScreenProvider
from src.modules.vision.look_at_screen import ScreenCaptureResult

SNAPSHOT_KEYS = {"text", "options", "auto", "updated_at_ms"}
REPLY_WITH_OPTIONS = "正文：\n（村口）你面前出现两条路。\n选项：\n1. 继续前进 (100, 200)\n2. 回头看看 (300, 200)"
REPLY_PLAIN = "正文：\n风静静地吹着。"


class StaticCapture:
    """每次返回同一帧（快速达成稳定判定）。"""

    def __init__(self, frame: bytes = b"frame-a") -> None:
        self.frame = frame
        self.calls = 0

    def capture(
        self, monitor_index: int, region: Optional[Tuple[int, int, int, int]] = None, max_width: Optional[int] = None
    ):
        self.calls += 1
        return ScreenCaptureResult(image=self.frame)


class GenCapture(StaticCapture):
    """每次返回互不相同的帧（画面持续变化，帧级去重永不命中）。"""

    def capture(self, monitor_index: int, region=None, max_width=None):
        self.calls += 1
        return ScreenCaptureResult(image=f"gen-{self.calls}".encode("utf-8"))


class BoomCapture:
    def capture(self, monitor_index: int, region=None, max_width=None):
        raise RuntimeError("screen unavailable")


class BoomReader:
    async def read_screen(self, *, want_options: bool) -> ScreenReading:
        raise RuntimeError("reader boom")


def fast_config(**overrides) -> TextAdvConfig:
    """毫秒级采样/超时与小去重上限的快节奏配置。"""
    config = TextAdvConfig(stability_sample_ms=10, stability_consecutive=2, stability_timeout_ms=120, no_change_limit=3)
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


async def wait_for(condition: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return condition()


def make_agent(
    *, reader=None, window=None, capture=None, config=None
) -> Tuple[TextAdvGameAgent, Dict[str, List[GamePayload]]]:
    bus = EventBus()
    collected: Dict[str, List[GamePayload]] = {"milestone": [], "report": [], "error": []}

    def collector(key: str):
        async def on_event(event_name: str, payload: GamePayload, source: str) -> None:
            collected[key].append(payload)

        return on_event

    for event, key in (
        (CoreEvents.GAME_MILESTONE, "milestone"),
        (CoreEvents.GAME_REPORT, "report"),
        (CoreEvents.GAME_ERROR, "error"),
    ):
        bus.on(event, collector(key), model_class=GamePayload)
    agent = TextAdvGameAgent(
        config or fast_config(),
        vision_reader=reader or FakeVisionReader(REPLY_PLAIN),  # type: ignore[arg-type]
        window_backend=window or FakeWindowBackend(),
        capture=capture or StaticCapture(),  # type: ignore[arg-type]
        input_backend=FakeInputBackend(),
        event_bus=bus,
    )
    return agent, collected


def test_metadata_builder_and_factory_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TextAdvGameAgent.name == "text_adv" and "文字冒险" in TextAdvGameAgent.description
    assert {CoreEvents.GAME_MILESTONE, CoreEvents.GAME_REPORT, CoreEvents.GAME_ERROR} <= set(
        TextAdvGameAgent.emits_events
    )

    built = build_text_adv_agent(
        config=fast_config(),
        vision_reader=FakeVisionReader(REPLY_PLAIN),
        window_backend=FakeWindowBackend(),
        capture=StaticCapture(),
        input_backend=FakeInputBackend(),
    )
    assert isinstance(built, TextAdvGameAgent) and built.auto is False  # 返回未启动实例

    monkeypatch.setattr("src.agents.text_adv.input.PyAutoGuiInputBackend", FakeInputBackend)
    registry = ToolRegistry()
    agent = instantiate_agent(
        "text_adv",
        {"stability_sample_ms": 10, "stability_consecutive": 2, "stability_timeout_ms": 120},
        deps=AgentDeps(llm_manager=object(), prompt_manager=object(), tool_registry=registry),
    )

    async def run() -> list[str]:
        await agent.start()
        names = sorted(spec.full_name for spec in registry.list_tools(for_agent="streamer"))
        await agent.stop()
        return names

    assert asyncio.run(run()) == ["text_adv_advance", "text_adv_choose", "text_adv_get_state", "text_adv_set_auto"]
    assert not any(
        spec.full_name.startswith("text_adv_") for spec in registry.list_tools(for_agent="streamer")
    )  # stop 后摘除


async def test_set_auto_is_idempotent_and_stop_cancels_the_loop() -> None:
    agent, _ = make_agent()
    await agent.set_auto(True)
    task = agent._observe_task
    assert task is not None and agent.auto is True
    await asyncio.sleep(0.05)
    await agent.set_auto(True)
    assert agent._observe_task is task and not task.done()  # 重复 set_auto(True) 不创建新 Task

    await agent.set_auto(False)
    assert agent.auto is False and (task.done() or task.cancelled())

    await agent.start()
    await agent.set_auto(True)
    running = agent._observe_task
    await agent.stop()
    assert running.done() or running.cancelled()
    assert agent.auto is False
    assert set(agent.get_state_snapshot()) == SNAPSHOT_KEYS and agent.get_state_snapshot()["auto"] is False


@pytest.mark.parametrize(
    ("reply", "kind", "other"),
    [(REPLY_WITH_OPTIONS, "report", "milestone"), (REPLY_PLAIN, "milestone", "report")],
    ids=["with-options", "plain"],
)
async def test_first_screen_is_reported_exactly_once(reply: str, kind: str, other: str) -> None:
    agent, collected = make_agent(reader=FakeVisionReader(reply))
    await agent.set_auto(True)
    assert await wait_for(lambda: len(collected[kind]) >= 1), "首屏未上报"
    await asyncio.sleep(0.1)
    assert len(collected[kind]) == 1 and collected[other] == []
    message = collected[kind][0].message
    if kind == "report":
        assert collected["report"][0].report_kind == "escalation"
        assert "text_adv_choose" in message and "1. 继续前进" in message
    else:
        assert "风静静地吹着" in message
    await agent.set_auto(False)


async def test_same_text_is_not_reported_twice_and_vlm_pauses_after_no_change_limit() -> None:
    reader = FakeVisionReader(REPLY_PLAIN)
    agent, collected = make_agent(reader=reader, capture=GenCapture())
    await agent.set_auto(True)
    assert await wait_for(lambda: len(collected["milestone"]) >= 1)
    # 读屏序列：首屏 1 次 + 无新屏计数 3 次，之后帧级检查，读屏次数定格在 4
    assert await wait_for(lambda: len(reader.calls) >= 4, timeout=3.0)
    await asyncio.sleep(0.1)
    paused_at = len(reader.calls)
    await asyncio.sleep(0.3)
    assert len(reader.calls) == paused_at and len(collected["milestone"]) == 1  # 暂停后不再消耗 VLM，同屏文本不重复上报
    await agent.set_auto(False)


async def test_perception_failure_is_quiet_while_loop_death_and_window_guard_report_one_error() -> None:
    quiet, quiet_events = make_agent(capture=BoomCapture())
    await quiet.set_auto(True)
    await asyncio.sleep(0.4)
    assert quiet_events["error"] == [] and quiet.auto is True  # 感知失败只记日志，循环存活
    assert quiet._observe_task is not None and not quiet._observe_task.done()
    await quiet.set_auto(False)

    dead, dead_events = make_agent(reader=BoomReader())
    await dead.set_auto(True)
    assert await wait_for(lambda: len(dead_events["error"]) >= 1)
    await asyncio.sleep(0.1)
    assert (
        len(dead_events["error"]) == 1 and dead.auto is False and dead._observe_task.done()
    )  # 循环整体死亡：恰好一条且 auto 回落
    await dead.set_auto(False)

    window = FakeWindowBackend()
    focus, focus_events = make_agent(window=window)
    await focus.set_auto(True)
    assert await wait_for(lambda: len(focus_events["milestone"]) >= 1)
    window.set_foreground(False)
    assert await wait_for(lambda: len(focus_events["error"]) >= 1)
    await asyncio.sleep(0.3)  # 连跑多轮不刷屏
    assert len(focus_events["error"]) == 1 and focus.auto is False and focus._observe_task.done()
    await focus.set_auto(False)

    missing, missing_events = make_agent(window=FakeWindowBackend(found=False))
    await missing.set_auto(True)
    assert await wait_for(lambda: len(missing_events["error"]) >= 1)
    await asyncio.sleep(0.1)
    assert len(missing_events["error"]) == 1 and missing.auto is False
    await missing.set_auto(False)


async def test_look_at_screen_capture_failure_degrades_without_game_error() -> None:
    """屏幕线契约：capture 异常 → success=True + text="" + 结构化 error 含 capture_failed；不发业务事件。"""
    registry = ToolRegistry()
    registry.register_provider(LookAtScreenProvider(config={}, screen_capture=BoomCapture()))
    result = await registry.invoke(ToolInvocation(tool_name="vision_look_at_screen", arguments={}, source="test"))
    assert result.success is True and result.structured_content.get("text") == ""
    assert "capture_failed" in str(result.structured_content.get("error") or "")


# 工具面：text_adv_advance / choose / set_auto / get_state


def make_tools(*, reader=None, window=None, capture=None, config=None, geom: MonitorGeometry | None = None):
    agent, collected = make_agent(reader=reader, window=window, capture=capture, config=config)
    input_backend = FakeInputBackend()
    resolved = geom or MonitorGeometry(0, 0, 1280, 720)
    provider = TextAdvToolProvider(
        agent=agent, input_backend=input_backend, monitor_resolver=lambda monitor_index: resolved
    )
    return agent, provider, input_backend, collected


async def _tool(provider: TextAdvToolProvider, name: str, **arguments):
    return await provider.invoke(ToolInvocation(tool_name=f"text_adv_{name}", arguments=arguments, source="test"))


async def test_tools_are_streamer_only_and_get_state_neither_captures_nor_reads() -> None:
    reader, capture = FakeVisionReader(REPLY_PLAIN), StaticCapture()
    _, provider, _, _ = make_tools(reader=reader, capture=capture)
    registry = ToolRegistry()
    registry.register_provider(provider, visible_to=build_text_adv_visible_to())
    assert {s.full_name for s in registry.list_tools(for_agent="streamer")} == {
        "text_adv_advance",
        "text_adv_choose",
        "text_adv_set_auto",
        "text_adv_get_state",
    }
    assert not any(s.full_name.startswith("text_adv_") for s in registry.list_tools(for_agent="minecraft"))

    result = await _tool(provider, "get_state")
    assert result.success is True and set(result.structured_content) == SNAPSHOT_KEYS
    assert reader.calls == [] and capture.calls == 0


async def test_set_auto_clicks_the_calibrated_button_once_and_only_flips_the_flag_when_uncalibrated() -> None:
    agent, provider, input_backend, _ = make_tools(config=fast_config(auto_button_xy=(640, 680)))
    assert (await _tool(provider, "set_auto", on=True)).success is True
    assert agent.get_state_snapshot()["auto"] is True
    await _tool(provider, "set_auto", on=True)  # AUTO 是切换按钮，重复 True 不能再点（二次点击会关掉）
    assert input_backend.calls.count("click:640,680,left") == 1
    await agent.set_auto(False)

    bare_agent, bare, bare_input, _ = make_tools()
    result = await _tool(bare, "set_auto", on=True)
    assert (
        result.success is True
        and bare_input.calls == []
        and "未标定" in str(result.structured_content.get("notice") or "")
    )
    assert bare_agent.get_state_snapshot()["auto"] is True
    await bare_agent.set_auto(False)


async def test_choose_clicks_the_computed_coordinates_and_returns_the_new_snapshot() -> None:
    reader = FakeVisionReader(REPLY_PLAIN)
    reader.queue_reply(REPLY_WITH_OPTIONS)  # 首次重读：选项屏
    reader.queue_reply(REPLY_PLAIN)  # 点击后重读：选项屏消失
    agent, provider, input_backend, collected = make_tools(reader=reader)
    result = await _tool(provider, "choose", option=1)
    assert result.success is True and input_backend.calls == ["click:100,200,left"]
    assert (
        result.structured_content["text"] == "风静静地吹着。" and collected["error"] == [] and agent.auto is False
    )  # 绝不自持循环


@pytest.mark.parametrize(
    ("reply", "option", "config", "keyword"),
    [
        (REPLY_PLAIN, 1, None, "无选项"),
        (REPLY_WITH_OPTIONS, 3, None, "不存在"),
        ("正文：\n你面前出现两条路。\n选项：\n1. 继续前进\n2. 回头看看", 1, None, "不可点"),  # 选项无坐标
        (REPLY_WITH_OPTIONS, 1, {"region": [900, 0, 320, 240]}, "越界"),  # 区域偏移使反算结果越出显示器矩形
    ],
    ids=["no-options", "invalid-index", "not-clickable", "out-of-bounds"],
)
async def test_choose_rejections_are_readable_failures_without_clicks_or_game_errors(
    reply, option, config, keyword
) -> None:
    _, provider, input_backend, collected = make_tools(
        reader=FakeVisionReader(reply), config=fast_config(**(config or {}))
    )
    result = await _tool(provider, "choose", option=option)
    assert result.success is False and keyword in (result.error_message or "")
    assert input_backend.calls == []
    await asyncio.sleep(0.05)
    assert collected["error"] == []


async def test_choose_never_blind_clicks_twice_when_the_screen_does_not_change() -> None:
    _, provider, input_backend, collected = make_tools(
        reader=FakeVisionReader(REPLY_WITH_OPTIONS)
    )  # 固定回复：重读仍是选项屏
    result = await _tool(provider, "choose", option=1)
    assert result.success is False and "画面未变化" in (result.error_message or "")
    assert len([c for c in input_backend.calls if c.startswith("click:")]) == 1
    await asyncio.sleep(0.05)
    assert collected["error"] == []


async def test_advance_presses_records_the_screen_and_fails_softly() -> None:
    agent, provider, input_backend, collected = make_tools(reader=FakeVisionReader(REPLY_PLAIN))
    ok = await _tool(provider, "advance")
    assert ok.success is True and "press:space" in input_backend.calls and ok.content == "风静静地吹着。"
    assert set(ok.structured_content) == SNAPSHOT_KEYS and agent.get_state_snapshot()["text"] == "风静静地吹着。"

    _, guarded, guarded_input, guarded_events = make_tools(window=FakeWindowBackend(found=False))
    refused = await _tool(guarded, "advance")  # 窗口护栏未通过：失败可读、未按键
    assert refused.success is False and "未找到游戏窗口" in (refused.error_message or "") and guarded_input.calls == []

    _, blind, blind_input, blind_events = make_tools(reader=BoomReader())
    soft = await _tool(blind, "advance")  # 感知失败：success=True + 结构化 capture_failed
    assert (
        soft.success is True
        and soft.content == ""
        and "capture_failed" in str(soft.structured_content.get("error") or "")
    )
    assert "press:space" in blind_input.calls

    await asyncio.sleep(0.05)
    for events in (collected, guarded_events, blind_events):
        assert events["error"] == [] and events["milestone"] == [] and events["report"] == []  # 工具调用本身零事件


# ---------------------------------------------------------------------------
# 工具面使用说明：告诉主播这是遥控型游戏
# ---------------------------------------------------------------------------


def test_tool_guide_declares_remote_control_shape() -> None:
    """玩法说明点名四个工具、说明选项上报后直接 choose、不走委派；经注册表按名单可取。"""
    _agent, provider, _input, _collected = make_tools()
    guide = provider.guide_for("streamer")
    for tool_name in ("text_adv_set_auto", "text_adv_choose", "text_adv_advance", "text_adv_get_state"):
        assert tool_name in guide
    assert "遥控型" in guide and "framework_delegate" in guide

    registry = ToolRegistry()
    registry.register_provider(provider, visible_to=build_text_adv_visible_to())
    assert registry.guides(for_agent="streamer") == [("text_adv", guide)]
