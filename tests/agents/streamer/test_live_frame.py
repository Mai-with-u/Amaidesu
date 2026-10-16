"""决策时看一眼直播画面：每窗第一次请求必挂最新帧、帧龄告警限频、非视觉与无帧不看。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.modules.vision.image_store import LatestFrame, VisionImageStore, set_image_store
from tests.agents.streamer.helpers import make_streamer_agent


def _slot(ref: str, at_ms: int) -> LatestFrame:
    """图像暂存 latest 槽位的替身快照。"""
    return LatestFrame(ref=ref, width=768, height=432, change=0.0, timestamp_ms=at_ms)


def _has_frame(messages: List[dict]) -> bool:
    return any(
        isinstance(m.get("content"), list) and any(part.get("type") == "image_ref" for part in m["content"])
        for m in messages
    )


class _Harness:
    """可控时钟 + 记录每次请求的 Planner。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, visual: bool = True, stale_warn_ms: int = 60_000) -> None:
        self.now = 1_000_000
        monkeypatch.setattr("src.agents.streamer.planner.now_ms", lambda: self.now)
        self.sent: List[List[dict]] = []
        self.frame: Optional[Any] = None
        self.replies: List[Any] = []
        self.warnings: List[str] = []
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
        self.planner.logger.warning = lambda text, **_: self.warnings.append(str(text))
        self.planner.bind_frame_provider(lambda: self.frame, stale_warn_ms=stale_warn_ms)

    async def window(self) -> List[List[dict]]:
        start = len(self.sent)
        await self.planner.plan([], proactive=True)
        return self.sent[start:]


async def test_frame_rides_first_request_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """画面挂在本窗第一次请求尾部；同窗后续步骤不再带。"""
    harness = _Harness(monkeypatch)
    harness.frame = _slot("img_a", harness.now - 2_000)
    call = SimpleNamespace(id="c1", name="memory_recall_session", arguments={})
    harness.replies = [
        SimpleNamespace(success=True, content="查一下", tool_calls=[call], request_id="r1"),
        SimpleNamespace(success=True, content="不说", tool_calls=[], request_id="r2"),
    ]
    requests = await harness.window()
    assert [_has_frame(messages) for messages in requests] == [True, False]
    note = requests[0][-1]["content"][0]["text"]
    assert note.startswith("【直播画面】") and "刚刚截取" in note


async def test_every_window_sees_frame_even_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """跨窗必挂：即使画面 ref 没变（静止画面重复看是设计内行为），每窗第一次请求都带帧。"""
    harness = _Harness(monkeypatch)
    harness.frame = _slot("img_a", harness.now)
    assert _has_frame((await harness.window())[0])
    harness.now += 20_000
    assert _has_frame((await harness.window())[0])
    harness.now += 20_000
    assert _has_frame((await harness.window())[0])


async def test_stale_frame_warns_rate_limited_but_still_attaches(monkeypatch: pytest.MonkeyPatch) -> None:
    """帧龄超过告警阈值照挂不误，但告警限频：5 分钟内多窗只记一条。"""
    harness = _Harness(monkeypatch, stale_warn_ms=15_000)
    harness.frame = _slot("img_stale", harness.now - 60_000)
    assert _has_frame((await harness.window())[0])
    assert len(harness.warnings) == 1 and "断流" in harness.warnings[0]
    harness.now += 20_000
    assert _has_frame((await harness.window())[0])
    assert len(harness.warnings) == 1
    harness.now += 5 * 60_000
    assert _has_frame((await harness.window())[0])
    assert len(harness.warnings) == 2


async def test_missing_frame_and_non_visual_model_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有画面、主模型看不了图，都不挂画面。"""
    harness = _Harness(monkeypatch)
    assert not _has_frame((await harness.window())[0])
    blind = _Harness(monkeypatch, visual=False)
    blind.frame = _slot("img_a", blind.now)
    assert not _has_frame((await blind.window())[0])


def _agent(enabled: bool):
    return make_streamer_agent({"live_frame": {"enabled": enabled}})


async def test_agent_binds_latest_slot_provider_only_when_enabled() -> None:
    """启用时 Planner 从图像暂存 latest 槽位取帧；关闭（默认）时不绑定画面来源。"""
    store = VisionImageStore()
    set_image_store(store)
    try:
        agent = _agent(True)
        assert agent._planner._frame_provider is not None
        assert agent._planner._frame_provider() is None
        store.put_latest("data:image/png;base64," + "x" * 64, width=64, height=36, change=0.1, timestamp_ms=2)
        assert agent._planner._frame_provider() is not None
        assert agent._planner._frame_provider().ref.startswith("img_")
        assert _agent(False)._planner._frame_provider is None
    finally:
        set_image_store(VisionImageStore())
