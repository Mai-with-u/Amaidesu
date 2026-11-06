"""递话事件化与续播重放：受理即发含全文的事件、重启接回场次时补回上一进程窗口的递话。

事件全量落盘事实源是诊断事件流文件（``logs/events/<日期>/<小时>.jsonl``）；
本文件手工构造该版式的样本目录驱动重放读取，不经真实落盘链路。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.streamer import StreamerPromptReceivedPayload
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import make_streamer_agent
from tests.config.helpers import wait_until

_LONG_PROMPT = "这是一条超过六十个字符的递话全文，用来验证事件载荷不截断——" + "补充内容" * 12


def _write_record(events_dir: Path, *, day: str, hour: str, event_name: str, ts_ms: int, data: dict) -> None:
    """按诊断事件流版式写一条记录（EventRecord 序列化行的必要字段子集）。"""
    day_dir = events_dir / day
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
    with open(day_dir / f"{hour}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


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


def test_resume_replays_prompts_from_previous_process_window(tmp_path: Path) -> None:
    """续播重放：只补上一进程窗口（上次 core.startup ~ 本次启动）内的递话，更早的不重放。"""
    events_dir = tmp_path / "events"
    t_this_startup = now_ms()
    t_prev_startup = t_this_startup - 60 * 60 * 1000
    t_earlier_startup = t_this_startup - 2 * 60 * 60 * 1000
    prev_day = datetime.fromtimestamp(t_prev_startup / 1000).strftime("%Y-%m-%d")
    prev_hour = datetime.fromtimestamp(t_prev_startup / 1000).strftime("%H")
    # 两个更早的进程边界 + 两个窗口的递话样本（写入上一次启动所在的小时文件；
    # 本次启动时刻在 now，其后的记录属新进程，不在重放窗口内）
    _write_record(
        events_dir, day=prev_day, hour=prev_hour, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_earlier_startup, data={}
    )
    _write_record(
        events_dir, day=prev_day, hour=prev_hour, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_prev_startup, data={}
    )
    _write_record(
        events_dir,
        day=prev_day,
        hour=prev_hour,
        event_name=CoreEvents.STREAMER_PROMPT_RECEIVED,
        ts_ms=t_earlier_startup + 100,
        data={"content": "更早窗口的递话", "source": "operator"},
    )
    _write_record(
        events_dir,
        day=prev_day,
        hour=prev_hour,
        event_name=CoreEvents.STREAMER_PROMPT_RECEIVED,
        ts_ms=t_prev_startup + 100,
        data={"content": "上一窗口的递话", "source": "operator"},
    )
    _write_record(
        events_dir,
        day=prev_day,
        hour=prev_hour,
        event_name=CoreEvents.STREAMER_PROMPT_RECEIVED,
        ts_ms=t_prev_startup + 200,
        data={"content": "与本进程队列重复的递话", "source": "operator"},
    )
    _write_record(
        events_dir,
        day=prev_day,
        hour=prev_hour,
        event_name=CoreEvents.STREAMER_PROMPT_RECEIVED,
        ts_ms=t_this_startup + 100,
        data={"content": "新进程里的递话", "source": "operator"},
    )

    agent = make_streamer_agent(events_dir=events_dir)
    agent._startup_ms = t_this_startup
    agent.receive_prompt(content="与本进程队列重复的递话", source="operator")
    agent._replay_prompts_from_event_log()

    assert list(agent._reminder_queue) == ["与本进程队列重复的递话", "上一窗口的递话"], (
        "上一窗口递话补进队列且排在本进程已有条目之后；更早窗口与新进程内的不重放"
    )


def test_resume_replay_degrades_silently_when_log_dir_missing(tmp_path: Path) -> None:
    """诊断流缺席（目录不存在）：降级为不重放，不抛错。"""
    agent = make_streamer_agent(events_dir=tmp_path / "no_such_dir")
    agent._startup_ms = 2_000_000
    agent._replay_prompts_from_event_log()
    assert list(agent._reminder_queue) == []
