"""结构化付费事件直落 viewer_facts——观众记忆的原料修复。

覆盖：事实文案口径（纯函数）/ handle_message 接线（gift/SC/guard 各落一条，
source_message_id 用消息自带 id）/ 同 msg_id 判重 / memory 未注入降级 /
真库集成（SimpleMemory 落库后 list_viewer_facts 可查，失败不阻断主链路）。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.streamer_agent import StreamerAgent, _paid_fact_text
from src.modules.events.payloads.room import GiftInfo, GuardInfo, RoomMessagePayload, RoomMessageUser, SuperChatInfo
from src.modules.memory.simple_memory import SimpleMemory
from src.modules.storage.database import SQLiteDatabase
from src.modules.time_utils import now_ms


def _build_agent(memory: object | None = None) -> StreamerAgent:
    llm = MagicMock()
    llm.call_tools = AsyncMock()
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    config = StreamerConfig.from_dict(
        {
            "proactive": {"enabled": False},
            "word_filter": {"enabled": False},
            "batch": {"batch_window_ms": 100, "tick_interval_ms": 50},
        }
    )
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        memory=memory,
    )


def _paid_message(message_type: str, message_id: str, **detail: object) -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type=message_type,  # type: ignore[arg-type]
        message_id=message_id,
        platform="bilibili",
        user=RoomMessageUser(id="u1", name="测试观众"),
        content=str(detail.pop("content", "")),
        timestamp_ms=now_ms(),
        **detail,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 文案口径（纯函数）
# ---------------------------------------------------------------------------


def test_gift_fact_text() -> None:
    msg = _paid_message("gift", "g1", gift=GiftInfo(name="小星星", count=1))
    assert _paid_fact_text(msg) == "送过礼物：小星星×1"


def test_sc_fact_text_with_price() -> None:
    msg = _paid_message("super_chat", "s1", content="晚安", sc=SuperChatInfo(total_price=30_000))
    assert _paid_fact_text(msg) == "SC：晚安（¥30）"


def test_sc_fact_text_without_content_keeps_price() -> None:
    msg = _paid_message("super_chat", "s2", sc=SuperChatInfo(total_price=5_000))
    assert _paid_fact_text(msg) == "SC（¥5）"


def test_guard_fact_text_by_level() -> None:
    for level, label in ((1, "总督"), (2, "提督"), (3, "舰长"), (0, "舰队")):
        msg = _paid_message("guard", f"gd{level}", guard=GuardInfo(guard_level=level))
        assert _paid_fact_text(msg) == f"开通{label}"


# ---------------------------------------------------------------------------
# 接线与判重
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paid_messages_record_viewer_facts() -> None:
    """三类付费消息各落一条事实：来源消息 ID、平台与用户随消息走。"""
    memory = MagicMock()
    memory.add_viewer_fact = AsyncMock(return_value=True)
    agent = _build_agent(memory)

    await agent.handle_message(_paid_message("gift", "g1", gift=GiftInfo(name="小星星", count=1)))
    await agent.handle_message(_paid_message("super_chat", "s1", content="晚安", sc=SuperChatInfo(total_price=30_000)))
    await agent.handle_message(_paid_message("guard", "gd1", guard=GuardInfo(guard_level=3)))
    await asyncio.gather(*agent._bg_tasks)

    assert memory.add_viewer_fact.await_count == 3
    calls = memory.add_viewer_fact.await_args_list
    assert calls[0].kwargs["fact_text"] == "送过礼物：小星星×1"
    assert calls[0].kwargs["source_message_id"] == "g1"
    assert calls[1].kwargs["fact_text"] == "SC：晚安（¥30）"
    assert calls[2].kwargs["fact_text"] == "开通舰长"
    assert all(c.kwargs["platform"] == "bilibili" and c.kwargs["user_id"] == "u1" for c in calls)


@pytest.mark.asyncio
async def test_duplicate_message_id_not_recorded_twice() -> None:
    """同一 msg_id 重复送达（事件重投）只落一次。"""
    memory = MagicMock()
    memory.add_viewer_fact = AsyncMock(return_value=True)
    agent = _build_agent(memory)
    msg = _paid_message("gift", "dup-1", gift=GiftInfo(name="小花花", count=2))

    await agent.handle_message(msg)
    await agent.handle_message(msg)
    await asyncio.gather(*agent._bg_tasks)

    assert memory.add_viewer_fact.await_count == 1


@pytest.mark.asyncio
async def test_memory_missing_degrades_silently() -> None:
    """memory 未注入（功能整体降级）时不落事实也不报错。"""
    agent = _build_agent(None)

    await agent.handle_message(_paid_message("gift", "g9", gift=GiftInfo(name="小星星", count=1)))

    assert len(agent._paid_fact_seen_ids) == 0
    assert not agent._bg_tasks


# ---------------------------------------------------------------------------
# 真库集成
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="paid-facts-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def memory(temp_db_path: Path) -> AsyncGenerator[SimpleMemory, None]:
    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    mem = SimpleMemory(store)
    await mem.initialize()
    yield mem
    await store.close()


@pytest.mark.asyncio
async def test_paid_facts_persist_and_queryable(memory: SimpleMemory) -> None:
    """真库链路：handle_message → add_viewer_fact → list_viewer_facts 可查。"""
    agent = _build_agent(memory)

    await agent.handle_message(
        _paid_message("super_chat", "s-live", content="晚安", sc=SuperChatInfo(total_price=30_000))
    )
    await asyncio.gather(*agent._bg_tasks)

    facts = await memory.list_viewer_facts(platform="bilibili", user_id="u1")
    assert [f.fact_text for f in facts] == ["SC：晚安（¥30）"]
    assert facts[0].source_message_id == "s-live"
