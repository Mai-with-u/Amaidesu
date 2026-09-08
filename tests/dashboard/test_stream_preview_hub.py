"""StreamPreviewHub 测试：缓冲 + 合帧推送 + 生命周期（ADR-008）。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.dashboard.stream_preview import StreamPreviewHub


def _make_hub(flush_interval_ms: int = 20, buffer_max: int = 100):
    ws = MagicMock()
    ws.broadcast_stream = AsyncMock(return_value=1)
    hub = StreamPreviewHub(ws, flush_interval_ms=flush_interval_ms, buffer_max=buffer_max)
    return hub, ws


def _delta(round_id="round_1", phase="planner", step=1, seq=1, text="想"):
    return {"round_id": round_id, "phase": phase, "step": step, "seq": seq, "text_delta": text}


@pytest.mark.asyncio
async def test_deltas_batched_into_single_stream_message():
    """flush 窗口内的多条 delta 合帧为一条流消息批量推送。"""
    hub, ws = _make_hub(flush_interval_ms=20)
    hub.on_thinking_delta(**_delta(seq=1, text="想"))
    hub.on_thinking_delta(**_delta(seq=2, text="了"))
    hub.on_thinking_delta(**_delta(phase="replyer", step=1, seq=3, text="说"))

    await asyncio.sleep(0.1)

    ws.broadcast_stream.assert_awaited_once()
    stream_type, data = ws.broadcast_stream.await_args.args
    assert stream_type == "thinking.delta"
    assert [d["text_delta"] for d in data["deltas"]] == ["想", "了", "说"]
    await hub.stop()


@pytest.mark.asyncio
async def test_flush_loop_exits_when_idle_and_restarts_on_new_delta():
    """空闲后 flush 循环退出（无泄漏 task）；新 delta 到达重新惰性启动。"""
    hub, ws = _make_hub(flush_interval_ms=20)
    hub.on_thinking_delta(**_delta(seq=1))
    await asyncio.sleep(0.1)
    first_task = hub._flush_task

    await asyncio.sleep(0.1)
    # 空闲退出：task 置空或已完成
    assert first_task is None or first_task.done() or hub._flush_task is None

    hub.on_thinking_delta(**_delta(seq=2))
    assert hub._flush_task is not None
    await asyncio.sleep(0.1)
    assert ws.broadcast_stream.await_count == 2
    await hub.stop()


@pytest.mark.asyncio
async def test_buffer_overflow_drops_oldest():
    """缓冲超限丢最旧（deque maxlen 语义）。"""
    hub, ws = _make_hub(flush_interval_ms=5000, buffer_max=3)
    for seq in range(1, 6):
        hub.on_thinking_delta(**_delta(seq=seq, text=f"t{seq}"))
    # 手动触发一次 flush（不等长窗口）
    batch = []
    while hub._buffer:
        batch.append(hub._buffer.popleft())
    assert [d["seq"] for d in batch] == [3, 4, 5]
    await hub.stop()


@pytest.mark.asyncio
async def test_stop_discards_pending_and_ignores_new_deltas():
    """stop 后残余缓冲丢弃，新 delta 被忽略。"""
    hub, ws = _make_hub(flush_interval_ms=5000)
    hub.on_thinking_delta(**_delta(seq=1))
    await hub.stop()
    hub.on_thinking_delta(**_delta(seq=2))
    assert ws.broadcast_stream.await_count == 0


@pytest.mark.asyncio
async def test_push_failure_is_swallowed():
    """推送异常吞掉（best-effort），flush 循环不崩。"""
    hub, ws = _make_hub(flush_interval_ms=20)
    ws.broadcast_stream = AsyncMock(side_effect=Exception("ws down"))
    hub.on_thinking_delta(**_delta(seq=1))
    await asyncio.sleep(0.1)
    ws.broadcast_stream.assert_awaited_once()
    await hub.stop()
