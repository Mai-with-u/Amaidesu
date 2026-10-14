"""决策时看一眼直播画面：只挂第一次请求尾部、同帧不重看、限频、过时与非视觉不看。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.payloads.room_state import RoomStateFramePayload


def _frame(ref: str, at_ms: int) -> RoomStateFramePayload:
    return RoomStateFramePayload(image_ref=ref, width=768, height=432, source="obs", timestamp_ms=at_ms)


def _has_frame(messages: List[dict]) -> bool:
    return any(
        isinstance(m.get("content"), list) and any(part.get("type") == "image_ref" for part in m["content"])
        for m in messages
    )


class _Harness:
    """可控时钟 + 记录每次请求的 Planner。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, visual: bool = True) -> None:
        self.now = 1_000_000
        monkeypatch.setattr("src.agents.streamer.planner.now_ms", lambda: self.now)
        self.sent: List[List[dict]] = []
        self.frame: Optional[RoomStateFramePayload] = None
        self.replies: List[Any] = []
        llm = MagicMock()
        llm.is_profile_visual = MagicMock(return_value=visual)

        async def generate(messages: list, **_: Any) -> Any:
            self.sent.append(list(messages))
            if self.replies:
                return self.replies.pop(0)
            return SimpleNamespace(success=True, content="不说", tool_calls=[], request_id="r")

        llm.generate = generate
        registry = MagicMock()
        registry.list_tools = MagicMock(return_value=[])
        self.planner = Planner(
            config={"planner_max_steps": 3},
            llm_service=llm,
            prompt_service=MagicMock(),
            room_state=RoomState(),
            tool_registry=registry,
        )
        self.planner.bind_frame_provider(lambda: self.frame, max_age_ms=15_000, min_interval_ms=15_000)

    async def window(self) -> List[List[dict]]:
        start = len(self.sent)
        await self.planner.plan([], proactive=True)
        return self.sent[start:]


async def test_frame_rides_first_request_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """画面挂在本窗第一次请求尾部；同窗后续步骤不再带。"""
    harness = _Harness(monkeypatch)
    harness.frame = _frame("img_a", harness.now - 2_000)
    call = SimpleNamespace(id="c1", name="memory_query_memory", arguments={})
    harness.replies = [
        SimpleNamespace(success=True, content="查一下", tool_calls=[call], request_id="r1"),
        SimpleNamespace(success=True, content="不说", tool_calls=[], request_id="r2"),
    ]
    requests = await harness.window()
    assert [_has_frame(messages) for messages in requests] == [True, False]
    note = requests[0][-1]["content"][0]["text"]
    assert note.startswith("【直播画面】") and "刚刚截取" in note


async def test_same_frame_not_shown_twice_and_interval_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    """看过的那一帧不再看；新帧来得太快（不到最短间隔）也先不看，够久了再看。"""
    harness = _Harness(monkeypatch)
    harness.frame = _frame("img_a", harness.now)
    assert _has_frame((await harness.window())[0])
    harness.now += 5_000
    assert not _has_frame((await harness.window())[0])
    harness.frame = _frame("img_b", harness.now)
    assert not _has_frame((await harness.window())[0])
    harness.now += 10_000
    harness.frame = _frame("img_c", harness.now)
    assert _has_frame((await harness.window())[0])


async def test_stale_or_missing_frame_and_non_visual_model_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """画面过时、没有画面、主模型看不了图，都不挂画面。"""
    harness = _Harness(monkeypatch)
    assert not _has_frame((await harness.window())[0])
    harness.frame = _frame("img_old", harness.now - 20_000)
    assert not _has_frame((await harness.window())[0])
    blind = _Harness(monkeypatch, visual=False)
    blind.frame = _frame("img_a", blind.now)
    assert not _has_frame((await blind.window())[0])


def _agent(enabled: bool) -> StreamerAgent:
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    return StreamerAgent(
        config=StreamerConfig.from_dict({"proactive": {"enabled": False}, "live_frame": {"enabled": enabled}}),
        llm_manager=MagicMock(),
        prompt_manager=prompt,
        event_bus=None,
        tool_registry=None,
    )


async def test_agent_keeps_latest_frame_and_binds_only_when_enabled() -> None:
    """启用时 Planner 拿到最新一帧；关闭（默认）时不绑定画面来源。"""
    agent = _agent(True)
    await agent._on_live_frame("room.state.frame", _frame("img_a", 1), "live_frame")
    await agent._on_live_frame("room.state.frame", _frame("img_b", 2), "live_frame")
    assert agent._planner._frame_provider is not None
    assert agent._planner._frame_provider().image_ref == "img_b"
    assert _agent(False)._planner._frame_provider is None
