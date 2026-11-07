"""续播接回已聊话题：程序重启后接回同一场，本场前面说出口的那几轮从诊断事件流重新记进打算账。

已聊话题只在内存里，重启后整份没了，续播场她会把早先讲过的段子又当新话题讲。续播经公开事件面驱动：
真实的 ``core.startup`` 划出本进程启动时刻，``live.started(resumed=True)`` 触发接回。诊断事件流样本按
``logs/events/<日期>/<小时>.jsonl`` 版式手工构造。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Dict, Optional

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.core import CoreStartupPayload
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.tools import ToolRegistry
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import FakeRoundExecutor, make_streamer_agent
from tests.config.helpers import wait_until

_MINUTE_MS = 60 * 1000


def _write_record(events_dir: Path, *, event_name: str, ts_ms: int, data: Dict) -> None:
    """按诊断事件流版式把一条记录写进它时刻所在的小时文件（EventRecord 必要字段子集）。"""
    dt = datetime.fromtimestamp(ts_ms / 1000)
    day_dir = events_dir / dt.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "id": f"rec_{event_name}_{ts_ms}",
        "type": event_name,
        "event_name": event_name,
        "timestamp_ms": ts_ms,
        "level": "info",
        "source": "test",
        "summary": event_name,
        "data": data,
    }
    with open(day_dir / f"{dt.strftime('%H')}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _decision(events_dir: Path, ts_ms: int, tag: str, speech: Optional[str], *, session: int = 7) -> None:
    """上一进程里的一轮决策记录：带场次编号、话题短名、话题摘要和说出口的台词（没开口为 None）。"""
    data = {
        "live_session_id": session,
        "topic_tag": tag,
        "topic_summary": f"{tag}的事",
        "reply_guidance": "",
        "speech": speech,
    }
    _write_record(events_dir, event_name=CoreEvents.PLANNER_DECISION, ts_ms=ts_ms, data=data)


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    event_bus = EventBus()
    yield event_bus
    await event_bus.cleanup()


async def test_resumed_session_gets_its_spoken_topics_back(bus: EventBus, tmp_path: Path) -> None:
    """本场说出口的四轮按先后接回：最近两轮留在清单里，更早的挪进"更早聊过"；没开口的、别的场次的不接。"""
    events_dir = tmp_path / "events"
    t_now = now_ms()
    _decision(events_dir, t_now - 9 * _MINUTE_MS, "拴绳", "拴绳又白背了一场")
    _decision(events_dir, t_now - 8 * _MINUTE_MS, "智能板", "这块板子到底干嘛的")
    _decision(events_dir, t_now - 7 * _MINUTE_MS, "起名", None)
    _decision(events_dir, t_now - 6 * _MINUTE_MS, "别场", "上一场的话", session=6)
    _decision(events_dir, t_now - 5 * _MINUTE_MS, "拴绳", "拴绳还是没用上")
    _decision(events_dir, t_now - 4 * _MINUTE_MS, "装门", "门总算装上了")

    executor = FakeRoundExecutor()
    agent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}, "planner_recent_intents_max": 2},
        event_bus=bus,
        events_dir=events_dir,
        round_executor=executor,
        tool_registry=ToolRegistry(),
    )
    await agent.start()
    try:
        await bus.emit(
            CoreEvents.CORE_STARTUP,
            CoreStartupPayload(event=CoreEvents.CORE_STARTUP, message="Amaidesu 组件装配完成"),
            source="Main",
        )
        await bus.wait_until_drained()
        await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=7, resumed=True), source="test")
        await wait_until(lambda: len(executor.calls) >= 1, what="续播接回那一轮执行")
    finally:
        await agent.stop()

    workbench = agent._planner.workbench
    assert [(intent.topic_tag, intent.speech) for intent in workbench.recent_intents] == [
        ("拴绳", "拴绳还是没用上"),
        ("装门", "门总算装上了"),
    ]
    assert workbench.earlier_topics == {"拴绳": 1, "智能板": 1}
