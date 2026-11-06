"""递话事件化与续播重放：受理与送达各发一条事件，重启接回场次时只补上一进程没送出去的递话。

事件全量落盘事实源是诊断事件流文件（``logs/events/<日期>/<小时>.jsonl``）；
本文件手工构造该版式的样本目录驱动重放读取，不经真实落盘链路。续播经公开事件面驱动：
真实的 ``core.startup`` 载荷划出本进程启动时刻，``live.started(resumed=True)`` 触发重放，
从接回那一轮决策窗取到的【运营提醒】看补回了哪些递话。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional

import pytest

from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.core import CoreStartupPayload
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.events.payloads.streamer import StreamerPromptDeliveredPayload, StreamerPromptReceivedPayload
from src.modules.tools import ToolRegistry
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import FakeRoundExecutor, make_streamer_agent
from tests.config.helpers import wait_until

_LONG_PROMPT = "这是一条超过六十个字符的递话全文，用来验证事件载荷不截断——" + "补充内容" * 12
_MINUTE_MS = 60 * 1000


def _write_record(events_dir: Path, *, event_name: str, ts_ms: int, data: dict, record_id: str = "") -> None:
    """按诊断事件流版式把一条记录写进它时刻所在的小时文件（EventRecord 序列化行的必要字段子集）。"""
    dt = datetime.fromtimestamp(ts_ms / 1000)
    day_dir = events_dir / dt.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "id": record_id or f"rec_{event_name}_{ts_ms}",
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


def _received(events_dir: Path, ts_ms: int, content: str) -> None:
    _write_record(
        events_dir,
        event_name=CoreEvents.STREAMER_PROMPT_RECEIVED,
        ts_ms=ts_ms,
        data={"content": content, "source": "operator"},
    )


def _delivered(events_dir: Path, ts_ms: int, *contents: str) -> None:
    _write_record(events_dir, event_name=CoreEvents.STREAMER_PROMPT_DELIVERED, ts_ms=ts_ms, data={"contents": contents})


@pytest.fixture
async def resume_bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    yield bus
    await bus.cleanup()


async def _resume_and_collect_reminders(
    bus: EventBus, events_dir: Path, *, startup: Optional[CoreStartupPayload] = None
) -> str:
    """真实启动事件 → 断线续播：返回续播后各决策窗取到的全部【运营提醒】文本。"""
    executor = FakeRoundExecutor()
    executor.drain_reminders = True  # 与真实执行器一样，开窗即取空提醒队列
    agent: StreamerAgent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}},
        event_bus=bus,
        events_dir=events_dir,
        round_executor=executor,
        tool_registry=ToolRegistry(),
    )
    await agent.start()
    try:
        await bus.emit(
            CoreEvents.CORE_STARTUP,
            startup or CoreStartupPayload(event=CoreEvents.CORE_STARTUP, message="Amaidesu 组件装配完成"),
            source="Main",
        )
        await bus.wait_until_drained()
        await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=7, resumed=True), source="test")
        await wait_until(lambda: len(executor.calls) >= 1, what="续播接回那一轮执行")
    finally:
        await agent.stop()
    return "\n".join(executor.window_reminders)


async def test_receive_prompt_emits_full_text_event(event_bus: EventBus) -> None:
    """递话受理成功即发 streamer.prompt_received，载荷带全文原文（不截断到 60 字符）。"""
    received: list[StreamerPromptReceivedPayload] = []

    async def _on_prompt(event_name: str, payload: StreamerPromptReceivedPayload, source: str) -> None:
        received.append(payload)

    event_bus.on(CoreEvents.STREAMER_PROMPT_RECEIVED, _on_prompt, model_class=StreamerPromptReceivedPayload)
    agent = make_streamer_agent(event_bus=event_bus)
    assert agent.receive_prompt(content=_LONG_PROMPT, source="operator") is True
    # 派发是同步入口挂出的后台任务，等其落地（wait_until_drained 在任务未启动前会提前返回）
    await wait_until(lambda: len(received) >= 1, what="递话事件送达")

    assert len(received) == 1
    assert received[0].content == _LONG_PROMPT, "载荷是全文原文，不是日志里的 60 字符截断"
    assert received[0].source == "operator"


async def test_drain_emits_delivered_event_with_all_contents(event_bus: EventBus) -> None:
    """决策窗取空提醒队列即发 streamer.prompt_delivered，载荷按入队先后列出这一窗取走的递话。"""
    delivered: list[StreamerPromptDeliveredPayload] = []

    async def _on_delivered(event_name: str, payload: StreamerPromptDeliveredPayload, source: str) -> None:
        delivered.append(payload)

    event_bus.on(CoreEvents.STREAMER_PROMPT_DELIVERED, _on_delivered, model_class=StreamerPromptDeliveredPayload)
    executor = FakeRoundExecutor()
    executor.drain_reminders = True
    agent = make_streamer_agent(event_bus=event_bus, round_executor=executor)
    agent.receive_prompt(content="先说这条", source="operator")
    agent.receive_prompt(content="再说这条", source="operator")
    # 递话必达：没开播、没说过话时这一 tick 直接成轮，执行器开窗取空队列
    await agent._maybe_flush()
    await wait_until(lambda: len(delivered) >= 1, what="送达事件送达")

    assert delivered[0].contents == ["先说这条", "再说这条"]
    assert "先说这条" in executor.window_reminders[0] and "再说这条" in executor.window_reminders[0]


async def test_real_startup_payload_logs_no_error(resume_bus: EventBus, tmp_path: Path, loguru_capture) -> None:
    """真实的 core.startup 载荷（不带时刻字段）：回调按当前时刻记启动，不再打 ERROR。"""
    await _resume_and_collect_reminders(resume_bus, tmp_path / "events")
    errors = [r for r in loguru_capture.records if r["level"] == "ERROR"]
    assert errors == [], f"启动与续播链路不应有 ERROR 日志：{errors}"


async def test_resume_replays_only_undelivered_recent_prompts(resume_bus: EventBus, tmp_path: Path) -> None:
    """只补上一进程结束前 10 分钟内收到、又没送达的递话；已送达的、太旧的、更早进程的都不补。"""
    events_dir = tmp_path / "events"
    t_now = now_ms()
    t_prev_startup = t_now - 60 * _MINUTE_MS
    t_prev_end = t_now - 2 * _MINUTE_MS
    _write_record(events_dir, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_now - 120 * _MINUTE_MS, data={})
    _received(events_dir, t_now - 100 * _MINUTE_MS, "更早进程的递话")
    _write_record(events_dir, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_prev_startup, data={})
    # 上一进程里：早先收到并已送进决策窗的、收到太久没送出的、临崩溃前收到还没送出的
    _received(events_dir, t_prev_end - 30 * _MINUTE_MS, "已送达的旧递话")
    _delivered(events_dir, t_prev_end - 29 * _MINUTE_MS, "已送达的旧递话")
    _received(events_dir, t_prev_end - 15 * _MINUTE_MS, "太旧没送出的递话")
    _received(events_dir, t_prev_end - 5 * _MINUTE_MS, "最近已送达的递话")
    _delivered(events_dir, t_prev_end - 4 * _MINUTE_MS, "最近已送达的递话")
    _received(events_dir, t_prev_end - 1 * _MINUTE_MS, "临崩溃前没送出的递话")
    # 上一进程最后一条记录（她最后一轮决策）：重放窗口按它往前算 10 分钟
    _write_record(events_dir, event_name=CoreEvents.STREAMER_STAGE, ts_ms=t_prev_end, data={"stage": "idle"})
    # 本进程自己的启动记录被记录器抢先落盘、时刻还早于启动回调：按事件 id 排除，不当成上一次启动
    own_startup = CoreStartupPayload(event=CoreEvents.CORE_STARTUP, message="Amaidesu 组件装配完成")
    _write_record(events_dir, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_now - 1, data={}, record_id=own_startup.id)

    text = await _resume_and_collect_reminders(resume_bus, events_dir, startup=own_startup)

    assert "临崩溃前没送出的递话" in text, "收到了还没送进决策窗的递话补回来"
    assert "已送达的旧递话" not in text and "最近已送达的递话" not in text, "已经送达的不重放"
    assert "太旧没送出的递话" not in text, "上一进程结束前 10 分钟以外收到的不重放"
    assert "更早进程的递话" not in text, "更早进程的递话不重放"


async def test_resume_replay_keeps_newest_in_order_when_over_capacity(resume_bus: EventBus, tmp_path: Path) -> None:
    """没送出的超过队列上限（5 条）：留最新的 5 条，按收到先后入队。"""
    events_dir = tmp_path / "events"
    t_now = now_ms()
    _write_record(events_dir, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_now - 30 * _MINUTE_MS, data={})
    for i in range(7):
        _received(events_dir, t_now - (8 - i) * _MINUTE_MS, f"递话{i}")

    text = await _resume_and_collect_reminders(resume_bus, events_dir)

    assert "递话0" not in text and "递话1" not in text, "超过上限时最早的两条不补"
    positions = [text.index(f"递话{i}") for i in range(2, 7)]
    assert positions == sorted(positions), "留下的 5 条按收到先后入队"


async def test_resume_replay_degrades_silently_when_log_dir_missing(resume_bus: EventBus, tmp_path: Path) -> None:
    """诊断流缺席（目录不存在）：降级为不重放，不抛错，接回那一轮照常执行。"""
    text = await _resume_and_collect_reminders(resume_bus, tmp_path / "no_such_dir")
    assert text.strip() == ""
