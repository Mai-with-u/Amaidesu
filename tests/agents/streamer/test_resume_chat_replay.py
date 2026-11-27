"""续播重放玩家聊天：只补上一进程最后一轮决策之后、她还没接的玩家发言，注入下一轮参考段一次。

续播经公开事件面驱动：真实的 ``core.startup`` 载荷划出本进程启动时刻，
``live.started(resumed=True)`` 触发重放，从接回那一轮决策回合取到的重放文本看补了哪些话。
诊断事件流样本按 ``logs/events/<日期>/<小时>.jsonl`` 版式手工构造。
"""

from __future__ import annotations

import asyncio
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
    """真实启动事件 → 断线续播：返回续播后第一轮决策回合取到的重放文本，并确认只注入一次。"""
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
    """最后一轮台词播出过的决策回合开始前的话她回过了不补；之后的尾巴补回，归属说明照实时路径保留。"""
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


@pytest.mark.parametrize(
    ("anchor_event", "anchor_data"),
    [
        (
            CoreEvents.STREAMER_SPEECH,
            {
                "utterance_id": "utt_comm_1",
                "round_id": "commentary_1",
                "text": "刚回到地面整理一下箱子！",
                "emotion": "happy",
                "emotion_intensity": 0.5,
                "source": "game_commentary",
            },
        ),
        (
            CoreEvents.GAME_SELF_CHATTED,
            {
                "game": "minecraft",
                "message": "收到啦，这就过去看看！",
            },
        ),
    ],
)
async def test_replay_anchors_on_game_lead_speech_and_self_chat(
    resume_bus: EventBus,
    tmp_path: Path,
    anchor_event: str,
    anchor_data: Dict[str, object],
) -> None:
    """游戏主控热重启期间无 planner.decision 时，认可 streamer.speech 与 game.self.chatted 作截断锚点，不兜底灌全段。"""
    events_dir = tmp_path / "events"
    t_now = now_ms()
    t_anchor = t_now - 90_000
    _write_record(events_dir, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_now - 60 * _MINUTE_MS, data={})
    _chat(events_dir, t_now - 180_000, "锚点前游戏主控已消化的话", sender="玩家甲")
    _write_record(events_dir, event_name=anchor_event, ts_ms=t_anchor, data=anchor_data)
    # 轻量进房欢迎只欢迎 B 站观众，不得将玩家在锚点后发的游戏公屏一并截断
    _chat(events_dir, t_anchor + 10_000, "锚点后刚发的游戏公屏", sender="玩家甲", sender_distance=10.0)
    _write_record(
        events_dir,
        event_name=CoreEvents.STREAMER_SPEECH,
        ts_ms=t_anchor + 20_000,
        data={
            "utterance_id": "utt_enter_1",
            "round_id": "enter_welcome_1",
            "text": "欢迎观众甲来直播间！",
            "emotion": "happy",
            "emotion_intensity": 0.5,
            "source": "enter_welcome",
        },
    )

    text = await _resume_and_collect_replay(resume_bus, events_dir)

    assert "锚点前游戏主控已消化的话" not in text
    assert "锚点后刚发的游戏公屏" in text


async def test_replay_filters_distant_unmentioned_chatter_while_keeping_nearby_and_mentioned(
    resume_bus: EventBus,
    tmp_path: Path,
) -> None:
    """续播重放按距离与自提及结构化事实过滤远处未点名的旁人闲聊，保留身边同伴、点名主播及无距离兼容记录。"""
    events_dir = tmp_path / "events"
    t_now = now_ms()
    _write_record(events_dir, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_now - 60 * _MINUTE_MS, data={})
    # 远处未点名旁人闲聊（>24 格且无 self mention）→ 必须过滤
    _chat(events_dir, t_now - 120_000, "远处两人闲聊今天去哪", sender="玩家甲", sender_distance=85.0)
    # 远处但显式点名主播（type="self"）→ 保留
    _chat(
        events_dir,
        t_now - 110_000,
        "麦麦你能过来帮我看下机器吗",
        sender="玩家乙",
        sender_distance=120.0,
        mentions=[{"name": "麦麦", "type": "self", "id": ""}],
    )
    # 身边同伴（<=24 格）→ 保留
    _chat(events_dir, t_now - 100_000, "我把铁锭放你旁边箱子啦", sender="玩家丙", sender_distance=12.0)
    # 旧版 Mod 未报距离（sender_distance=None）→ 保守保留
    _chat(events_dir, t_now - 90_000, "无距离字段的兼容消息", sender="玩家丁")

    text = await _resume_and_collect_replay(resume_bus, events_dir)

    assert "远处两人闲聊今天去哪" not in text
    assert "麦麦你能过来帮我看下机器吗" in text
    assert "[提及麦麦]" in text
    assert "我把铁锭放你旁边箱子啦" in text
    assert "无距离字段的兼容消息" in text


async def test_resume_replay_locks_cutoff_before_await_when_core_startup_advances(
    resume_bus: EventBus,
    tmp_path: Path,
) -> None:
    """热重启续播在 _on_live_started await 期间遇到 core.startup 推进 _startup_ms 时，重放仍锁定入口截止时刻，不混入本进程新事件。"""
    events_dir = tmp_path / "events"
    t_boot = 1_800_000_000_000
    clock_now = t_boot

    def _clock() -> int:
        return clock_now

    # 上一进程启动、早期已消化锚点与锚点后的未回复尾巴
    _write_record(events_dir, event_name=CoreEvents.CORE_STARTUP, ts_ms=t_boot - 30 * _MINUTE_MS, data={})
    _chat(events_dir, t_boot - 120_000, "上一进程锚点前已消化的话", sender="玩家甲", sender_distance=8.0)
    _write_record(
        events_dir,
        event_name=CoreEvents.STREAMER_SPEECH,
        ts_ms=t_boot - 60_000,
        data={
            "utterance_id": "utt_prev_comm",
            "round_id": "commentary_prev",
            "text": "上一进程刚解说完！",
            "emotion": "happy",
            "emotion_intensity": 0.5,
            "source": "game_commentary",
        },
    )
    _chat(events_dir, t_boot - 15_000, "上一进程真正没回的尾巴", sender="玩家甲", sender_distance=8.0)
    _write_record(
        events_dir,
        event_name=CoreEvents.STREAMER_PROMPT_RECEIVED,
        ts_ms=t_boot - 10_000,
        data={"content": "上一进程未送达递话", "source": "operator"},
    )

    startup_seen = asyncio.Event()

    async def _on_startup_seen(_name: str, _payload: CoreStartupPayload, _source: str) -> None:
        startup_seen.set()

    class _StartupRaceMaigoBridge:
        """在 _on_live_started 的 await 期间模拟 core.startup 并发到达并写入本进程新事件。"""

        inject_topic_cards = False
        inject_person_cues = False

        def update_save_id(self, save_id: object) -> None:
            del save_id

        def render_brief_extension(self) -> str:
            return ""

        async def ensure_checked_out(self, payload: object, *, save_id: object = None) -> None:
            nonlocal clock_now
            del payload, save_id
            # 模拟热重启中采集器已启动、本进程新事件先落盘，随后 core.startup 把 _startup_ms 推进到 t_boot + 5s
            _write_record(
                events_dir,
                event_name=CoreEvents.STREAMER_SPEECH,
                ts_ms=t_boot + 2_000,
                data={
                    "utterance_id": "utt_new_proc",
                    "round_id": "commentary_new",
                    "text": "本进程新发出的解说",
                    "emotion": "happy",
                    "emotion_intensity": 0.5,
                    "source": "game_commentary",
                },
            )
            _chat(events_dir, t_boot + 3_000, "本进程刚收到的新游戏公屏", sender="玩家乙", sender_distance=6.0)
            _write_record(
                events_dir,
                event_name=CoreEvents.STREAMER_PROMPT_RECEIVED,
                ts_ms=t_boot + 3_500,
                data={"content": "本进程刚收到的新递话", "source": "operator"},
            )
            clock_now = t_boot + 5_000
            await resume_bus.emit(
                CoreEvents.CORE_STARTUP,
                CoreStartupPayload(event=CoreEvents.CORE_STARTUP, message="Amaidesu 并发启动完成"),
                source="Main",
            )
            # 当前正处在 live.started 回调内，不能调用 bus.wait_until_drained() 自等；改等后注册的观测回调落位
            await asyncio.wait_for(startup_seen.wait(), timeout=2.0)

    executor = FakeRoundExecutor()
    executor.drain_reminders = True
    agent = make_streamer_agent(
        {"background": {"enabled": False}, "mouth": {"enabled": False}, "resume_replay_window_ms": 5 * _MINUTE_MS},
        event_bus=resume_bus,
        events_dir=events_dir,
        round_executor=executor,
        tool_registry=ToolRegistry(),
        maigo_bridge=_StartupRaceMaigoBridge(),
        clock=_clock,
    )
    await agent.start()
    resume_bus.on(CoreEvents.CORE_STARTUP, _on_startup_seen, model_class=CoreStartupPayload)
    try:
        # 模拟热重启前置恢复：live.started(resumed=True) 先于 core.startup 触发
        await resume_bus.emit(
            CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=80, resumed=True), source="test"
        )
        await wait_until(lambda: len(executor.calls) >= 1, what="续播接回那一轮执行")
        await resume_bus.wait_until_drained()
    finally:
        await agent.stop()

    replayed_chat = executor.window_resume_replay[0]
    replayed_reminders = executor.window_reminders[0]
    assert "上一进程真正没回的尾巴" in replayed_chat, "本进程新发声不应覆盖上一进程锚点把旧尾巴吃掉"
    assert "上一进程锚点前已消化的话" not in replayed_chat
    assert "本进程刚收到的新游戏公屏" not in replayed_chat, "本进程新游戏聊天绝不能混入上一进程重放"
    assert "上一进程未送达递话" in replayed_reminders
    assert "本进程刚收到的新递话" not in replayed_reminders, "本进程新递话绝不能误当上一进程递话重放"
