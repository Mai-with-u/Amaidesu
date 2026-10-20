"""主播备忘录观测事件（streamer.notebook.changed）：写入即广播、读回不发、失败不反噬。"""

from __future__ import annotations

import pytest

from src.agents.streamer.memory import (
    StreamerNotebook,
    build_notebook_tool_provider,
    emit_notebook_changed,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.notebook import StreamerNotebookChangedPayload
from src.modules.tools.models import ToolInvocation


class _RecordingBus:
    """记录 emit 调用的最小事件总线替身（签名对齐 EventBus.emit）。"""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, object, str]] = []

    async def emit(self, event_name: str, payload: object, source: str = "") -> None:
        self.emitted.append((event_name, payload, source))


class _ExplodingBus:
    """emit 即抛错的总线替身（验证观测事件失败不阻断写入路径）。"""

    async def emit(self, event_name: str, payload: object, source: str = "") -> None:
        raise RuntimeError("总线不可用")


@pytest.mark.asyncio
async def test_write_emits_notebook_changed_with_full_text() -> None:
    bus = _RecordingBus()
    provider = build_notebook_tool_provider(StreamerNotebook(), event_bus=bus)
    await provider.invoke(ToolInvocation(tool_name="streamer_notebook", arguments={"content": "答应观众画新皮肤"}))
    assert len(bus.emitted) == 1
    event_name, payload, source = bus.emitted[0]
    assert event_name == CoreEvents.STREAMER_NOTEBOOK_CHANGED
    assert source == "streamer_notebook"
    assert isinstance(payload, StreamerNotebookChangedPayload)
    assert payload.notebook == "答应观众画新皮肤"
    # 场次归属由事件总线盖章拦截器注入，发布方不填
    assert payload.live_session_id == 0


@pytest.mark.asyncio
async def test_read_does_not_emit() -> None:
    bus = _RecordingBus()
    notebook = StreamerNotebook()
    notebook.set("已有结论")
    provider = build_notebook_tool_provider(notebook, event_bus=bus)
    await provider.invoke(ToolInvocation(tool_name="streamer_notebook", arguments={}))
    assert bus.emitted == []


@pytest.mark.asyncio
async def test_overwrite_and_clear_by_empty_write_emit_latest_text() -> None:
    bus = _RecordingBus()
    provider = build_notebook_tool_provider(StreamerNotebook(), event_bus=bus)
    await provider.invoke(ToolInvocation(tool_name="streamer_notebook", arguments={"content": "第一版"}))
    await provider.invoke(ToolInvocation(tool_name="streamer_notebook", arguments={"content": ""}))
    texts = [payload.notebook for _name, payload, _source in bus.emitted]
    assert texts == ["第一版", ""]


@pytest.mark.asyncio
async def test_emit_failure_does_not_break_write() -> None:
    notebook = StreamerNotebook()
    provider = build_notebook_tool_provider(notebook, event_bus=_ExplodingBus())
    result = await provider.invoke(ToolInvocation(tool_name="streamer_notebook", arguments={"content": "写入仍要成功"}))
    assert result.content == "写入仍要成功"
    assert notebook.read() == "写入仍要成功"


@pytest.mark.asyncio
async def test_emit_helper_without_bus_is_noop() -> None:
    # 极简场景（无总线）下清空点直接调用助手也不出错
    await emit_notebook_changed(None, "")
