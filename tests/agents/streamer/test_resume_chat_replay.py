"""续播窗口重放玩家聊天：重启前窗口内的玩家发言注入下一轮参考段，消费后不重复。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from src.agents.streamer.planner import Planner, PlannerConfig
from src.agents.streamer.room_state import RoomState
from src.modules.events.names import CoreEvents
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import make_streamer_agent


def _write_chat_record(events_dir: Path, *, ts_ms: int, data: dict) -> None:
    """按诊断事件流版式写一条 game.chat.received 记录（EventRecord 必要字段子集）。"""
    dt = datetime.fromtimestamp(ts_ms / 1000)
    day_dir = events_dir / dt.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "id": f"rec_chat_{ts_ms}",
        "type": CoreEvents.GAME_CHAT_RECEIVED,
        "event_name": CoreEvents.GAME_CHAT_RECEIVED,
        "timestamp_ms": ts_ms,
        "level": "info",
        "source": "test",
        "summary": "聊天",
        "data": data,
    }
    with open(day_dir / f"{dt.strftime('%H')}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_replay_keeps_window_player_chats_and_drains_once(tmp_path: Path) -> None:
    """窗口内的玩家发言进缓冲且一次取空；更早的、系统消息、私聊不重放。"""
    events_dir = tmp_path / "events"
    t_startup = now_ms()
    window_ms = 5 * 60 * 1000
    _write_chat_record(
        events_dir,
        ts_ms=t_startup - window_ms - 1000,
        data={"game": "minecraft", "kind": "player", "sender": "老王", "content": "窗口之前的发言"},
    )
    _write_chat_record(
        events_dir,
        ts_ms=t_startup - 60_000,
        data={"game": "minecraft", "kind": "player", "sender": "阿杰", "content": "重启前说的最后一句话"},
    )
    _write_chat_record(
        events_dir,
        ts_ms=t_startup - 30_000,
        data={"game": "minecraft", "kind": "system", "sender": "", "content": "系统公告不算玩家的话"},
    )
    _write_chat_record(
        events_dir,
        ts_ms=t_startup - 20_000,
        data={"game": "minecraft", "kind": "player", "sender": "阿杰", "content": "悄悄话", "private": True},
    )

    agent = make_streamer_agent({"resume_replay_window_ms": window_ms}, events_dir=events_dir)
    agent._startup_ms = t_startup
    agent._load_resume_chat_replay()

    text = agent._drain_resume_replay()
    assert "重启前说的最后一句话" in text, "窗口内的玩家发言进注入段"
    assert re.search(r"^\d{2}:\d{2} 玩家 阿杰：重启前说的最后一句话$", text, flags=re.M), "每行是 时间 + 发言者 + 原文"
    assert "窗口之前的发言" not in text, "窗口之前的发言不重放"
    assert "系统公告" not in text, "系统消息不重放"
    assert "悄悄话" not in text, "私聊不重放"
    assert agent._drain_resume_replay() == "", "一次性缓冲：消费后不再注入"


def test_zero_window_disables_replay(tmp_path: Path) -> None:
    """resume_replay_window_ms=0 关闭重放：有事件记录也不装载。"""
    events_dir = tmp_path / "events"
    t_startup = now_ms()
    _write_chat_record(
        events_dir,
        ts_ms=t_startup - 1000,
        data={"game": "minecraft", "kind": "player", "sender": "老王", "content": " hi"},
    )
    agent = make_streamer_agent({"resume_replay_window_ms": 0}, events_dir=events_dir)
    agent._startup_ms = t_startup
    agent._load_resume_chat_replay()
    assert agent._drain_resume_replay() == ""


async def test_planner_reference_contains_replay_section_only_when_present() -> None:
    """参考段：有重放内容时出现【进程重启前漏掉的话】，没有时整段缺席。"""
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
    assert "【进程重启前漏掉的话】" in with_replay and "回来接着玩" in with_replay
    without_replay, _ = await planner._assemble_reference([], None, None, **common)
    assert "【进程重启前漏掉的话】" not in without_replay
