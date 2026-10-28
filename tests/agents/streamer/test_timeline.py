"""主播时间线：只追加、按章压缩、前缀稳定、兜底与场次边界。"""

from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerContextConfig
from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.planner import Planner
from src.agents.streamer.replyer import Replyer
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.timeline import (
    CHAPTER_MESSAGE_TYPE,
    GAME_MESSAGE_TYPE,
    StreamerTimeline,
    TimelineTurn,
    note_turn,
)
from src.modules.llm.payload import Response

pytestmark = pytest.mark.usefixtures("chars_as_tokens")  # conftest 共享 fixture：按字符数计 token


def _turns(count: int, start: int = 1) -> List[TimelineTurn]:
    """观众与主播交替发言，每条约 100 字。"""
    turns = []
    for i in range(start, start + count):
        role = "viewer" if i % 2 else "assistant"
        turns.append(
            TimelineTurn(
                role=role,
                content=f"第{i}句" + "聊" * 100,
                sender_name="小明" if role == "viewer" else "",
                message_id=f"m{i}",
                timestamp_ms=1_791_000_000_000 + i * 60_000,
            )
        )
    return turns


def _timeline(generate: Any = None) -> StreamerTimeline:
    """预算 4000 token：提前整理线 2800、回落线 1600（窗口未配置，按上限计）。"""
    llm = MagicMock()
    llm.get_profile_context_window = MagicMock(return_value=0)
    llm.generate = generate or AsyncMock(
        return_value=Response(success=True, content="小明一直在聊天，主播答应下播前唱歌。")
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    return StreamerTimeline(
        budget=StreamerContextConfig(max_tokens=4000),
        llm_service=llm,
        prompt_service=prompt,
        profiles=("planner", "replyer"),
        summary_profile="summary",
    )


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_entries_append_in_first_seen_order_without_duplicates() -> None:
    """同一条记录跨窗只进一次；新记录追加在尾部，旧前缀不动。"""
    timeline = _timeline()
    turns = _turns(6)
    first = await timeline.project(turns)
    second = await timeline.project([*turns, *_turns(2, start=7)])
    assert [t.message_id for t in first] == [f"m{i}" for i in range(1, 7)]
    assert second[: len(first)] == first and len(second) == 8
    # 没有消息 ID 的调试对话按角色 + 时刻 + 正文识别，重复读不重复进
    loose = TimelineTurn(role="viewer", content="不落库的话", timestamp_ms=5)
    third = await timeline.project([*turns, *_turns(2, start=7), loose])
    fourth = await timeline.project([*turns, *_turns(2, start=7), loose])
    assert third == fourth and third[-1] is loose


async def test_chapter_replaces_oldest_segment_after_background_summary() -> None:
    """越过提前整理线后台写前情提要；写好后的下一窗换上，最近 20 条保持原文。"""
    timeline = _timeline()
    turns = _turns(30)
    before = await timeline.project(turns)
    assert len(before) == 30 and timeline._pending.busy
    await _settle()
    after = await timeline.project(turns)
    assert after[0].message_type == CHAPTER_MESSAGE_TYPE
    assert after[0].content.startswith("[前情提要·第 1 章") and "答应下播前唱歌" in after[0].content
    assert [t.message_id for t in after[1:]] == [f"m{i}" for i in range(11, 31)]
    # 下一章生成时把上一章交给模型衔接，记录里不再出现已压缩的条目
    request = timeline._llm.generate.call_args.args[0]
    assert "第1句" in request and "第11句" not in request


async def test_prefix_stays_stable_between_checkpoints() -> None:
    """换章之后只追加：下一窗的历史以上一窗的历史为前缀。"""
    timeline = _timeline()
    await timeline.project(_turns(30))
    await _settle()
    settled = await timeline.project(_turns(30))
    grown = await timeline.project([*_turns(30), *_turns(2, start=31)])
    assert grown[: len(settled)] == settled


async def test_hard_fold_when_summary_never_arrives() -> None:
    """摘要一直不回来、历史涨到预算两倍时，同步把最老一段换成占位说明，最近记录仍是原文。"""
    hang = asyncio.Event()

    async def slow(*_: Any, **__: Any) -> Response:
        await hang.wait()
        return Response(success=True, content="不会用到")

    timeline = _timeline(generate=slow)
    await timeline.project(_turns(30))
    view = await timeline.project(_turns(80))
    assert view[0].message_type == CHAPTER_MESSAGE_TYPE and "来不及整理" in view[0].content
    assert view[-1].message_id == "m80"
    await timeline.reset()


async def test_failed_summary_is_retried_later() -> None:
    """摘要失败不换章，下次越过提前整理线时重新发起。"""
    generate = AsyncMock(
        side_effect=[Response(success=False, error="超时"), Response(success=True, content="第二次成功")]
    )
    timeline = _timeline(generate=generate)
    await timeline.project(_turns(30))
    await _settle()
    retry = await timeline.project(_turns(30))
    assert retry[0].message_type != CHAPTER_MESSAGE_TYPE and timeline._pending.busy
    await _settle()
    done = await timeline.project(_turns(30))
    assert "第二次成功" in done[0].content


async def test_scope_change_restarts_timeline() -> None:
    """读到的场次变了（换场没收到开播事件）时从空白重新长，不把上一场的话带进来。"""
    timeline = _timeline()
    await timeline.project(_turns(5), scope=1)
    fresh = await timeline.project(_turns(2, start=101), scope=2)
    assert [t.message_id for t in fresh] == ["m101", "m102"]


def test_planner_maps_chapter_as_plain_user_message() -> None:
    """Planner 把前情提要当一条原样的 user 消息，不加昵称与消息 ID。"""
    planner = Planner(config=None, llm_service=MagicMock(), prompt_service=MagicMock(), room_state=RoomState())
    chapter = TimelineTurn(role="user", content="[前情提要·第 1 章]\n聊了天", message_type=CHAPTER_MESSAGE_TYPE)
    messages = planner._build_dialogue_messages([], [chapter, *_turns(1)])
    assert messages[0] == {"role": "user", "content": "[前情提要·第 1 章]\n聊了天"}


async def test_replyer_renders_chapter_line_verbatim() -> None:
    """Replyer 的聊天记录里，前情提要与游戏记录原样成行，不被拆成"某人说"。"""
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="好"))
    replyer = Replyer(config={}, llm_service=llm, prompt_service=prompt)
    chapter = TimelineTurn(role="user", content="[前情提要·第 1 章]\n主播: 答应唱歌", message_type=CHAPTER_MESSAGE_TYPE)
    note = note_turn("游戏·minecraft·report", "交付: 木头砍好了", 0)
    assert note.message_type == GAME_MESSAGE_TYPE and note.content == "[游戏·minecraft·report] 交付: 木头砍好了"
    plan = DecisionPlan(should_reply=True, target="", topic_summary="唱歌", reply_guidance="兑现")
    await replyer.generate(plan, [], history=[chapter, note])
    dialogue = next(
        call.kwargs["dialogue_prompt"] for call in prompt.render.call_args_list if "dialogue_prompt" in call.kwargs
    )
    assert dialogue == "[前情提要·第 1 章]\n主播: 答应唱歌\n[游戏·minecraft·report] 交付: 木头砍好了"
