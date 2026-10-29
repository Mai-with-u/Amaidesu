"""形象动作工具：目录、有意的动作、平台片段、保持表情、未知名字随结果返回目录、工具组说明"""

from __future__ import annotations

import random
from typing import Any, List

import pytest

from src.modules.avatar.act_tool import GUIDE, AvatarActProvider
from src.modules.avatar.tracking import TrackingConfig, TrackingRuntime
from src.modules.avatar.tracking.channels import GAZE_X, HEAD_PITCH, HEAD_YAW, eye_openness
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from tests.helpers import FakeClock


def _runtime(clock: FakeClock) -> TrackingRuntime:
    config = TrackingConfig(blink_enabled=False, gaze_enabled=False, head_wander_enabled=False, mood_enabled=False)
    return TrackingRuntime(config, clock=clock.now_ms, rng=random.Random(0), run_loop=False)


async def _call(provider: AvatarActProvider, tool: str, **arguments: Any):
    return await provider.invoke(ToolInvocation(tool_name=tool, arguments=arguments, source="test"))


async def _frames(runtime: TrackingRuntime, clock: FakeClock, ms: int) -> List[dict]:
    frames = []
    for _ in range(ms // 33):
        clock.tick(33)
        frames.append(await runtime.tick_once())
    return frames


def test_tools_are_platform_free_and_visible_to_streamer(fake_clock: FakeClock) -> None:
    registry = ToolRegistry()
    registry.register_provider(AvatarActProvider(_runtime(fake_clock)))
    names = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
    assert names == {"avatar_list_actions", "avatar_act"}
    assert dict(registry.guides(for_agent="streamer"))["avatar"] == GUIDE


async def test_catalog_lists_gestures_hold_and_platform_clips(fake_clock: FakeClock) -> None:
    runtime = _runtime(fake_clock)
    triggered: List[str] = []

    async def trigger(name: str) -> bool:
        triggered.append(name)
        return True

    runtime.register_clips("vts", lambda: ["挥手动画"], trigger)
    provider = AvatarActProvider(runtime)
    catalog = (await _call(provider, "avatar_list_actions")).structured_content["actions"]
    kinds = {entry["name"]: entry["kind"] for entry in catalog}
    assert kinds["点头"] == "动作" and kinds["保持表情"] == "表情" and kinds["挥手动画"] == "vts 片段"

    assert (await _call(provider, "avatar_act", action="挥手动画")).success is True
    assert triggered == ["挥手动画"]


@pytest.mark.parametrize(("action", "channel", "threshold"), [("点头", HEAD_PITCH, -4.0), ("摇头", HEAD_YAW, -3.0)])
async def test_gestures_move_the_head(fake_clock: FakeClock, action: str, channel: str, threshold: float) -> None:
    runtime = _runtime(fake_clock)
    await runtime.tick_once()
    assert (await _call(AvatarActProvider(runtime), "avatar_act", action=action)).success is True
    assert min(frame[channel] for frame in await _frames(runtime, fake_clock, 1200)) < threshold


async def test_wink_closes_only_one_eye(fake_clock: FakeClock) -> None:
    runtime = _runtime(fake_clock)
    await runtime.tick_once()
    assert (await _call(AvatarActProvider(runtime), "avatar_act", action="眨一只眼")).success is True
    frames = await _frames(runtime, fake_clock, 900)
    assert min(eye_openness(frame, "Left") for frame in frames) < 0.1  # 一只眼闭上
    assert min(eye_openness(frame, "Right") for frame in frames) == pytest.approx(1.0)  # 另一只照常睁着
    assert eye_openness(frames[-1], "Left") > 0.9  # 眨完睁回来


async def test_look_at_chat_and_hold_expression(fake_clock: FakeClock) -> None:
    runtime = _runtime(fake_clock)
    provider = AvatarActProvider(runtime)
    await _call(provider, "avatar_act", action="看弹幕")
    assert (await _frames(runtime, fake_clock, 600))[-1][GAZE_X] == pytest.approx(0.45)

    held = await _call(provider, "avatar_act", action="保持表情", emotion="angry", seconds=20)
    assert held.success is True and runtime.current_emotion() == "angry"
    fake_clock.tick(19_000)
    assert runtime.current_emotion() == "angry"  # 演"假装生气一会儿"：保持到给定秒数
    fake_clock.tick(2_000)
    assert runtime.current_emotion() == "neutral"
    assert (await _call(provider, "avatar_act", action="保持表情", emotion="不存在")).success is False


async def test_unknown_action_returns_catalog(fake_clock: FakeClock) -> None:
    result = await _call(AvatarActProvider(_runtime(fake_clock)), "avatar_act", action="后空翻")
    assert result.success is False and "点头" in result.structured_content["available_actions"]
