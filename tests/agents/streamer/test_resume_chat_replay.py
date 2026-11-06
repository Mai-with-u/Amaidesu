"""续播重放玩家聊天：只补上一进程最后一轮决策之后、她还没接的玩家发言，注入下一轮参考段一次。

续播经公开事件面驱动：真实的 ``core.startup`` 载荷划出本进程启动时刻，
``live.started(resumed=True)`` 触发重放，从接回那一轮决策窗取到的重放文本看补了哪些话。
诊断事件流样本按 ``logs/events/<日期>/<小时>.jsonl`` 版式手工构造。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Dict
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.planner import Planner, PlannerConfig
from src.agents.streamer.room_state import RoomState
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.core import CoreStartupPayload
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.tools import ToolRegistry
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import FakeRoundExecutor, make_streamer_agent
from tests.config.helpers import wait_until

_MINUTE_MS = 60 * 1000
_UNVERIFIED_NOTE = "没有发言人凭据，名字只是聊天栏上显示的，不一定是这个人说的"


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


def _chat(events_dir: Path, ts_ms: int, content: str, **fields: object) -> None:
    data = {"game": "minecraft", "kind": "player", "sender": "阿杰", "content": content, **fields}
    _write_record(events_dir, event_name=CoreEvents.GAME_CHAT_RECEIVED, ts_ms=ts_ms, data=data)


def _round(events_dir: Path, open_ms: int, round_id: str, *, utterance_id: str = "", aired: bool = False) -> None:
    """上一进程里的一轮决策：开窗（planning）→ 决策记录（有台词时带 utterance_id）→ 可选的开始出声。"""
    _write_record(
        events_dir,
        event_name=CoreEvents.STREAMER_STAGE,
        ts_ms=open_ms,
        data={"stage": "planning", "round_id": round_id},
    )
    _write_record(
        events_dir,
        event_name=CoreEvents.PLANNER_DECISION,
        ts_ms=open_ms + 4000,
        data={"round_id": round_id, "utterance_id": utterance_id or None},
    )
    if aired:
        _write_record(
            events_dir,
            event_name=CoreEvents.TTS_UTTERANCE_STARTED,
            ts_ms=open_ms + 5000,
            data={"utterance_id": utterance_id, "speech_text": "台词", "engine": "test"},
        )


@pytest.fixture
async def resume_bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    yield bus
    await bus.cleanup()


async def _resume_and_collect_replay(bus: EventBus, events_dir: Path, *, window_ms: int = 5 * _MINUTE_MS) -> str:
    """真实启动事件 → 断线续播：返回续播后第一轮决策窗取到的重放文本，并确认只注入一次。"""
    executor = FakeRoundExecutor()
    executor.drain_reminders = True  # 与真实执行器一样，开窗即取空递话队列与续播重放缓冲
    agent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}, "resume_replay_window_ms": window_ms},
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
    assert all(text == "" for text in executor.window_resume_replay[1:]), "一次性缓冲：只注入一轮"
    return executor.window_resume_replay[0]


async def test_replay_skips_answered_chat_and_keeps_unanswered_tail(resume_bus: EventBus, tmp_path: Path) -> None:
    """最后一轮台词播出过的决策开窗前的话她回过了不补；之后的尾巴补回，归属说明照实时路径保留。"""
    events_dir = tmp_path / "events"
    t_now = now_ms()
    t_open = t_now - 3 * _MINUTE_MS
    _write_record(events_dir, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_now - 60 * _MINUTE_MS, data={})
    _chat(events_dir, t_now - 4 * _MINUTE_MS, "重启前已经回过的话")
    # 台词播出过的一轮：开窗那一刻账本里的聊天都被取走，她开口回了
    _round(events_dir, t_open, "rnd_1", utterance_id="utt_1", aired=True)
    # 她回上一句的时候刚到、还没进那一窗的话：界线按开窗时刻算，这句要补
    _chat(events_dir, t_open + 2000, "她开口时刚到的话")
    # 静默收场的一轮、台词没来得及播出的一轮都不算回过：它们取走的话照样要补
    _chat(events_dir, t_now - 160_000, "静默那一轮取走的话")
    _round(events_dir, t_now - 150_000, "rnd_2")
    _chat(events_dir, t_now - 130_000, "没播出那一轮取走的话")
    _round(events_dir, t_now - 120_000, "rnd_3", utterance_id="utt_3")
    _chat(events_dir, t_now - 90_000, "名字没核实的话", note=_UNVERIFIED_NOTE)
    _chat(events_dir, t_now - 80_000, "系统公告不算玩家的话", kind="system", sender="")
    _chat(events_dir, t_now - 70_000, "悄悄话", private=True)

    text = await _resume_and_collect_replay(resume_bus, events_dir)

    assert "重启前已经回过的话" not in text, "最后一轮播出过台词的决策已经回过的话不补"
    assert re.search(r"^\d{2}:\d{2} 玩家 阿杰：她开口时刚到的话$", text, flags=re.M), "时间 + 发言者 + 原话"
    assert "静默那一轮取走的话" in text and "没播出那一轮取走的话" in text, "没开口回的轮次不算回过"
    assert f"玩家 阿杰（{_UNVERIFIED_NOTE}）：名字没核实的话" in text, "归属说明与实时路径逐字一致"
    assert "系统公告" not in text, "系统消息不重放"
    assert "悄悄话" not in text, "私聊不重放"


async def test_replay_falls_back_to_window_when_no_aired_round(
    resume_bus: EventBus, tmp_path: Path, loguru_capture
) -> None:
    """找不到台词播出过的一轮（这里是没开 TTS）：兜底补上一进程结束前 5 分钟的整段，记下兜底原因。"""
    events_dir = tmp_path / "events"
    t_now = now_ms()
    _write_record(events_dir, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_now - 60 * _MINUTE_MS, data={})
    _chat(events_dir, t_now - 20 * _MINUTE_MS, "上一进程结束前很久的话")
    _chat(events_dir, t_now - 12 * _MINUTE_MS, "开口之前的话")
    _round(events_dir, t_now - 11 * _MINUTE_MS, "rnd_1", utterance_id="utt_1")
    _chat(events_dir, t_now - 10 * _MINUTE_MS, "上一进程最后几分钟的话")
    # 上一进程最后一条记录：兜底窗口按它往前算 5 分钟
    _write_record(events_dir, event_name=CoreEvents.ROOM_STATE_WATCHED_COUNT, ts_ms=t_now - 8 * _MINUTE_MS, data={})

    text = await _resume_and_collect_replay(resume_bus, events_dir)

    assert "上一进程最后几分钟的话" in text and "开口之前的话" in text, "兜底按 5 分钟整段补，可能含她回过的"
    assert "上一进程结束前很久的话" not in text
    assert any("找不到台词播出过的决策轮" in r["message"] for r in loguru_capture.records), "兜底原因写进日志"


async def test_zero_window_disables_replay(resume_bus: EventBus, tmp_path: Path) -> None:
    """resume_replay_window_ms=0 关闭重放：有事件记录也不装载。"""
    events_dir = tmp_path / "events"
    _chat(events_dir, now_ms() - 1000, "hi")
    assert await _resume_and_collect_replay(resume_bus, events_dir, window_ms=0) == ""


async def test_planner_reference_contains_replay_section_only_when_present() -> None:
    """参考段：有重放内容时出现【重启前可能还没回的话】并写明回过的别再回，没有时整段缺席。"""
    planner = Planner(
        config=PlannerConfig(planner_max_steps=2),
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=MagicMock(),
        context_enabled=False,
    )
    common: dict = {"forced": False, "proactive": False}
    with_replay, _ = await planner._assemble_reference(
        [], None, None, resume_replay="12:30 玩家 阿杰：回来接着玩", **common
    )
    assert "【重启前可能还没回的话】" in with_replay and "回来接着玩" in with_replay
    assert "也可能已经回过" in with_replay and "回过的别再回" in with_replay, "标题如实说明：可能已经回过"
    assert "程序中断期间" not in with_replay, "不再把重启前还活着时的话说成中断期间的话"
    without_replay, _ = await planner._assemble_reference([], None, None, **common)
    assert "【重启前可能还没回的话】" not in without_replay
