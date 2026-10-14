"""直播画面采集器：变化才发布、隔太久补发、取帧失败不中断、图像只以句柄进事件。"""

from __future__ import annotations

import base64
import io
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from src.modules.collectors.frame.live_frame_collector import GrabbedFrame, LiveFrameCollector, frame_change
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room_state import RoomStateFramePayload
from src.modules.vision.image_store import VisionImageStore, set_image_store


def _frame(shade: int) -> GrabbedFrame:
    """一张纯色 PNG（灰度 shade）。"""
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), (shade, shade, shade)).save(buffer, format="PNG")
    data = buffer.getvalue()
    return GrabbedFrame(f"data:image/png;base64,{base64.b64encode(data).decode()}", data, 64, 36)


class _Grabber:
    """按脚本吐帧的取帧后端；脚本项为 None 表示取不到，为异常表示来源不可用。"""

    def __init__(self, script: List[object]) -> None:
        self.script = list(script)
        self.closed = False

    async def grab(self) -> Optional[GrabbedFrame]:
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _store() -> VisionImageStore:
    store = VisionImageStore()
    set_image_store(store)
    yield store
    set_image_store(VisionImageStore())


def _collector(script: List[object], **config: object) -> tuple[LiveFrameCollector, MagicMock]:
    bus = MagicMock()
    bus.emit = AsyncMock()
    collector = LiveFrameCollector(config={"refresh_ms": 0, **config}, event_bus=bus, grabber=_Grabber(script))
    return collector, bus


def test_frame_change_measures_mean_gray_difference() -> None:
    assert frame_change(None, [0, 0]) == 1.0
    assert frame_change([10, 10], [10, 10]) == 0.0
    assert frame_change([0, 0], [255, 255]) == 1.0


async def test_publishes_first_and_changed_frames_only(_store: VisionImageStore) -> None:
    """首帧必发；画面没明显变化不发；明显变化再发，事件只带暂存句柄。"""
    collector, bus = _collector([_frame(10), _frame(12), _frame(200)])
    first = await collector.sample_once()
    assert isinstance(first, RoomStateFramePayload) and first.change == 1.0 and first.source == "obs"
    assert await collector.sample_once() is None
    third = await collector.sample_once()
    assert third is not None and third.change > 0.5
    assert [call.args[0] for call in bus.emit.await_args_list] == [CoreEvents.ROOM_STATE_FRAME] * 2
    assert _store.get(third.image_ref).startswith("data:image/png;base64,")
    assert "base64" not in third.model_dump_json()


async def test_unchanged_frame_republished_after_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """画面一直没变，隔 refresh_ms 仍补发一次，订阅方知道画面还是新的。"""
    clock = {"now": 1_000_000}
    monkeypatch.setattr("src.modules.collectors.frame.live_frame_collector.now_ms", lambda: clock["now"])
    collector, _ = _collector([_frame(10), _frame(10), _frame(10)], refresh_ms=60_000)
    assert await collector.sample_once() is not None
    clock["now"] += 30_000
    assert await collector.sample_once() is None
    clock["now"] += 30_000
    assert await collector.sample_once() is not None


async def test_grab_failure_does_not_stop_sampling() -> None:
    """来源不可用（OBS 没开）只记日志，恢复后照常发布。"""
    collector, bus = _collector([ConnectionRefusedError("OBS 未开"), None, _frame(10)])
    assert await collector.sample_once() is None
    assert await collector.sample_once() is None
    assert await collector.sample_once() is not None
    assert bus.emit.await_count == 1


async def test_stop_closes_grabber() -> None:
    collector, _ = _collector([])
    await collector.stop()
    assert collector._grabber.closed  # type: ignore[attr-defined]


def test_config_defaults_are_constructible() -> None:
    """物化按注册表补缺段依赖无参构造：默认截 OBS 当前节目场景、5 秒采样、768 宽。"""
    config = LiveFrameCollector.ConfigSchema()
    assert (config.source, config.obs_source, config.interval_ms, config.max_width) == ("obs", "", 5000, 768)
