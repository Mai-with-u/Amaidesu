"""主播记忆测试：场次回顾 / 常客名册 / 记忆工具 / StreamerAgent 接线。

真库（临时 SQLite）+ mock LLM：
- ``SessionRecapWriter``：原料来自库表；空场次与未结账不写；LLM 失败不写；
  启动补写只补已结束且缺回顾的；注入渲染排除本场
- ``RegularsRosterWriter``：互动门槛、每场人数上限、原料水位（同场重复收尾不空转）、
  LLM 失败逐人降级；付费直读原料行
- ``build_memory_tool_provider``：往前第 n 场、排除本场、按昵称查印象
- ``StreamerAgent``：两仓储齐全才注册记忆工具；开播装入上场回顾；收播写回顾并更新名册
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.memory import (
    RegularsRosterWriter,
    SessionRecapWriter,
    build_memory_tool_provider,
    collect_payment_material,
)
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.storage.database import SQLiteDatabase
from src.modules.tools import ToolInvocation, ToolRegistry


# ---------------------------------------------------------------------------
# 夹具与造数
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="streamer-memory-"))
    yield td / "memory.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    db = SQLiteDatabase(temp_db_path)
    await db.initialize()
    yield db
    await db.close()


def _llm(text: str = "", *, success: bool = True) -> MagicMock:
    llm = MagicMock()
    response = MagicMock()
    response.success = success
    response.content = text
    llm.generate = AsyncMock(return_value=response)
    return llm


def _prompts() -> MagicMock:
    prompt_manager = MagicMock()
    prompt_manager.render = MagicMock(return_value="PROMPT")
    return prompt_manager


async def _viewer_says(store: SQLiteDatabase, pk: int, user_id: str, name: str, text: str, ts: int) -> None:
    """造一条观众弹幕：live_chat 行 + viewers 统计（与记账器写穿同效果）。"""
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=ts,
        sender_role="viewer",
        sender_id=user_id,
        sender_name=name,
        content=text,
        message_type="danmaku",
        platform="bilibili",
        message_id=f"{user_id}-{ts}",
    )
    await store.viewers.upsert_viewer_message(platform="bilibili", user_id=user_id, user_name=name, timestamp_ms=ts)


async def _streamer_says(store: SQLiteDatabase, pk: int, text: str, ts: int) -> None:
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=ts,
        sender_role="assistant",
        sender_id="麦麦",
        sender_name="麦麦",
        content=text,
        message_type="speech",
    )


async def _ended_session(store: SQLiteDatabase, *, started: int = 1_000, ended: int = 9_000) -> int:
    pk = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=started)
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=ended)
    return pk


def _recap_writer(store: SQLiteDatabase, llm: MagicMock) -> SessionRecapWriter:
    return SessionRecapWriter(
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        recap_repo=store.recaps,
        topic_repo=store.topics,
        event_repo=store.events,
        llm_service=llm,
        prompt_manager=_prompts(),
        max_length=200,
    )


def _roster_writer(store: SQLiteDatabase, llm: MagicMock, **kwargs: Any) -> RegularsRosterWriter:
    return RegularsRosterWriter(
        chat_repo=store.chat,
        viewer_repo=store.viewers,
        profile_repo=store.viewer_profiles,
        llm_service=llm,
        prompt_manager=_prompts(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 场次回顾
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recap_material_from_tables_and_written(store: SQLiteDatabase) -> None:
    """原料含主播发言 / 话题轨迹 / 游戏进展 / 活跃观众；写回后可按场取。"""
    pk = await _ended_session(store)
    await _streamer_says(store, pk, "答应大家下场去挖钻石", 2_000)
    await _viewer_says(store, pk, "u1", "老王", "想看挖钻石", 2_500)
    await store.topics.insert_timeline_summary(live_session_id=pk, start_ms=1_000, end_ms=3_000, summary="在聊挖钻石")
    await store.events.insert_game_event(
        live_session_id=pk, game="minecraft", event_type="milestone", message="造好了熔炉", timestamp_ms=4_000
    )
    llm = _llm("这场我答应大家下场去挖钻石。" + "长" * 400)

    text = await _recap_writer(store, llm).write_recap(pk)

    assert text is not None and len(text) == 200  # 按上限截断
    prompt = llm.generate.await_args.args[0]
    for marker in ("答应大家下场去挖钻石", "在聊挖钻石", "造好了熔炉", "老王（1 条）"):
        assert marker in prompt
    row = await store.recaps.get_recap(live_session_id=pk)
    assert row is not None and row["recap_text"] == text


@pytest.mark.asyncio
async def test_recap_skipped_for_empty_unended_or_llm_failure(store: SQLiteDatabase) -> None:
    empty_pk = await _ended_session(store)
    llm = _llm("不该写")
    assert await _recap_writer(store, llm).write_recap(empty_pk) is None
    llm.generate.assert_not_awaited()

    live_pk = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=1_000)
    await _streamer_says(store, live_pk, "还在播", 2_000)
    assert await _recap_writer(store, llm).write_recap(live_pk) is None  # 未结账

    failed_pk = await _ended_session(store)
    await _streamer_says(store, failed_pk, "说了点什么", 2_000)
    assert await _recap_writer(store, _llm(success=False)).write_recap(failed_pk) is None
    assert await store.recaps.get_recap(live_session_id=failed_pk) is None


@pytest.mark.asyncio
async def test_recap_backfill_and_render_recent(store: SQLiteDatabase) -> None:
    """启动补写只补最近一场缺回顾的；注入渲染最近一场在前、排除本场。"""
    old_pk = await _ended_session(store, started=1_000, ended=2_000)
    new_pk = await _ended_session(store, started=3_000, ended=4_000)
    for pk in (old_pk, new_pk):
        await _streamer_says(store, pk, f"第{pk}场的话", 1_500)
    writer = _recap_writer(store, _llm("补写的回顾"))

    assert await writer.backfill_missing(limit=1) == [new_pk]
    assert await store.recaps.get_recap(live_session_id=new_pk) is not None
    assert await store.recaps.get_recap(live_session_id=old_pk) is None

    rendered = await writer.render_recent(count=1)
    assert rendered.startswith("【") and "补写的回顾" in rendered
    assert await writer.render_recent(count=1, exclude_session_id=new_pk) == ""
    assert await writer.render_recent(count=0) == ""


# ---------------------------------------------------------------------------
# 常客名册
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_roster_threshold_watermark_and_batch(store: SQLiteDatabase) -> None:
    pk = await _ended_session(store)
    for i in range(3):
        await _viewer_says(store, pk, "u_a", "老A", f"A的第{i}句", 1_000 + i)
        await _viewer_says(store, pk, "u_b", "老B", f"B的第{i}句", 2_000 + i)
    await _viewer_says(store, pk, "u_new", "新人", "第一次来", 3_000)
    llm = _llm("常客印象")

    writer = _roster_writer(store, llm, min_interactions=3, batch_max=1)
    assert await writer.update_for_session(pk) == 1  # 两人达标，上限 1 人
    assert await store.viewer_profiles.get_profile_text(platform="bilibili", user_id="u_new") is None

    writer = _roster_writer(store, llm, min_interactions=3, batch_max=5)
    assert await writer.update_for_session(pk) == 1  # 已更新的那位水位之后没新话，只剩另一位
    assert await writer.update_for_session(pk) == 0  # 同场重复收尾不空转
    assert llm.generate.await_count == 2
    assert await store.viewer_profiles.count_profiles() == 2


@pytest.mark.asyncio
async def test_roster_llm_failure_leaves_profile_absent(store: SQLiteDatabase) -> None:
    pk = await _ended_session(store)
    for i in range(3):
        await _viewer_says(store, pk, "u1", "老王", f"第{i}句", 1_000 + i)
    assert await _roster_writer(store, _llm(success=False)).update_for_session(pk) == 0
    assert await store.viewer_profiles.get_profile(platform="bilibili", user_id="u1") is None


@pytest.mark.asyncio
async def test_payment_material_lines() -> None:
    """原料行含付费汇总、三类明细与最近一次付费时的身份快照；仓储缺失降级为空。"""
    chat_repo = MagicMock()
    chat_repo.summarize_user_contributions = AsyncMock(
        return_value={"gift_total_amount": 2000, "sc_total_amount": 50_000}
    )
    medal = {"fans_medal_level": 21, "fans_medal_name": "粉丝团"}
    chat_repo.list_user_gifts = AsyncMock(
        return_value=[{"timestamp_ms": 100, "gift_name": "小星星", "quantity": 3, "guard_level": 3, **medal}]
    )
    chat_repo.list_user_super_chats = AsyncMock(
        return_value=[{"timestamp_ms": 200, "message": "加油", "guard_level": 0, **medal}]
    )
    chat_repo.list_user_guards = AsyncMock(
        return_value=[{"timestamp_ms": 300, "guard_level": 3, "guard_num": 1, "guard_unit": "月", **medal}]
    )

    joined = "\n".join(await collect_payment_material(chat_repo, "u1"))

    assert "累计付费约 52 元" in joined
    assert "送出礼物 小星星×3" in joined and "发送 SC「加油」" in joined and "开通舰长（1月）" in joined
    assert "最近一次付费时的身份：舰长、粉丝团21 级牌" in joined
    assert await collect_payment_material(None, "u1") == []


# ---------------------------------------------------------------------------
# 记忆工具
# ---------------------------------------------------------------------------


async def _invoke(provider: Any, name: str, **arguments: Any) -> str:
    result = await provider.invoke(ToolInvocation(tool_name=name, arguments=arguments))
    assert result.success, result.error_message
    return result.content


@pytest.mark.asyncio
async def test_recall_session_tool(store: SQLiteDatabase) -> None:
    for sid, started in ((1, 1_000), (2, 5_000), (3, 9_000)):
        await store.recaps.upsert_recap(
            live_session_id=sid, started_at_ms=started, ended_at_ms=started + 60_000, recap_text=f"第{sid}场回顾"
        )
    current = {"pk": 3}
    provider = build_memory_tool_provider(
        recap_repo=store.recaps,
        profile_repo=store.viewer_profiles,
        current_session_provider=lambda: current["pk"],
    )
    assert {spec.full_name for spec in provider.list_tools()} == {"memory_recall_session", "memory_recall_viewer"}

    assert "第2场回顾" in await _invoke(provider, "memory_recall_session")  # 本场（3）排除
    assert "第1场回顾" in await _invoke(provider, "memory_recall_session", n_back=2)
    assert "没有那么早" in await _invoke(provider, "memory_recall_session", n_back=5)

    current["pk"] = None  # 未开播：上一场就是最近一场
    assert "第3场回顾" in await _invoke(provider, "memory_recall_session")


@pytest.mark.asyncio
async def test_recall_viewer_tool(store: SQLiteDatabase) -> None:
    await store.viewers.upsert_viewer_message(platform="bilibili", user_id="u1", user_name="老王", timestamp_ms=1)
    await store.viewer_profiles.upsert_profile(
        platform="bilibili", user_id="u1", profile_text="老粉，爱看挖矿", last_compressed_at_ms=1
    )
    provider = build_memory_tool_provider(
        recap_repo=store.recaps, profile_repo=store.viewer_profiles, viewer_repo=store.viewers
    )

    assert "老粉，爱看挖矿" in await _invoke(provider, "memory_recall_viewer", nickname="老王")
    assert "老粉，爱看挖矿" in await _invoke(provider, "memory_recall_viewer", user_id="u1")
    assert "没找到" in await _invoke(provider, "memory_recall_viewer", nickname="路人")
    assert "请提供" in await _invoke(provider, "memory_recall_viewer")


# ---------------------------------------------------------------------------
# StreamerAgent 接线
# ---------------------------------------------------------------------------


def _build_agent(
    store: SQLiteDatabase | None, llm: MagicMock, registry: ToolRegistry | None = None, **memory: Any
) -> StreamerAgent:
    config = StreamerConfig.from_dict(
        {
            "proactive": {"enabled": False},
            "word_filter": {"enabled": False},
            "background": {"enabled": False},
            "memory": memory,
        }
    )
    repos: dict[str, Any] = {}
    if store is not None:
        repos = {
            "chat_repo": store.chat,
            "sessions_repo": store.sessions,
            "topic_repo": store.topics,
            "viewer_repo": store.viewers,
            "viewer_profile_repo": store.viewer_profiles,
            "recap_repo": store.recaps,
            "event_repo": store.events,
        }
    return StreamerAgent(config=config, llm_manager=llm, prompt_manager=_prompts(), tool_registry=registry, **repos)


@pytest.mark.asyncio
async def test_agent_registers_memory_tools_only_with_repos(store: SQLiteDatabase) -> None:
    registry = ToolRegistry()
    agent = _build_agent(store, _llm(), registry)
    agent._register_tools()
    assert registry.has("memory_recall_session") and registry.has("memory_recall_viewer")
    assert registry.visible_to_of("memory_recall_session") == ["streamer"]

    bare_registry = ToolRegistry()
    bare = _build_agent(None, _llm(), bare_registry)
    bare._register_tools()
    assert not bare_registry.has("memory_recall_session")


@pytest.mark.asyncio
async def test_live_started_loads_previous_recap(store: SQLiteDatabase) -> None:
    """开播：读出最近一场回顾（排除本场）装进参考段；关注入时为空。"""
    await store.recaps.upsert_recap(
        live_session_id=1, started_at_ms=1_000, ended_at_ms=2_000, recap_text="上场挖到钻石"
    )
    agent = _build_agent(store, _llm())
    await agent._on_live_started("live.started", LiveStartedPayload(live_session_id=2), "test")
    assert "上场挖到钻石" in agent._session_recap_text()

    muted = _build_agent(store, _llm(), recap_inject_count=0)
    await muted._on_live_started("live.started", LiveStartedPayload(live_session_id=2), "test")
    assert muted._session_recap_text() == ""


@pytest.mark.asyncio
async def test_bootstrap_backfills_recap_roster_and_section(store: SQLiteDatabase) -> None:
    """启动善后：补写漏掉的回顾、补跑该场名册，并把回顾装进上场回顾段。"""
    pk = await _ended_session(store)
    await _streamer_says(store, pk, "下场继续挖", 2_000)
    for i in range(3):
        await _viewer_says(store, pk, "u1", "老王", f"第{i}句", 3_000 + i)
    agent = _build_agent(store, _llm("补写结果"))

    await agent._bootstrap_recaps()

    assert await store.recaps.get_recap(live_session_id=pk) is not None
    assert await store.viewer_profiles.get_profile_text(platform="bilibili", user_id="u1") == "补写结果"
    assert "补写结果" in agent._session_recap_text()


@pytest.mark.asyncio
async def test_live_ended_settles_recap_and_roster(store: SQLiteDatabase) -> None:
    """收播：后台写本场回顾 + 更新常客；空场次与关闭开关时不整理。"""
    pk = await _ended_session(store)
    await _streamer_says(store, pk, "下场继续挖", 2_000)
    for i in range(3):
        await _viewer_says(store, pk, "u1", "老王", f"第{i}句", 3_000 + i)
    agent = _build_agent(store, _llm("整理结果"))

    await agent._on_live_ended("live.ended", LiveEndedPayload(live_session_id=pk), "test")
    await asyncio.gather(*agent._bg_tasks)

    assert await store.recaps.get_recap(live_session_id=pk) is not None
    assert await store.viewer_profiles.get_profile_text(platform="bilibili", user_id="u1") == "整理结果"

    other_pk = await _ended_session(store)
    await _streamer_says(store, other_pk, "又一场", 2_000)
    llm = _llm("不该写")
    disabled = _build_agent(store, llm, recap_enabled=False, roster_enabled=False)
    await disabled._on_live_ended("live.ended", LiveEndedPayload(live_session_id=other_pk), "test")
    await disabled._on_live_ended(
        "live.ended", LiveEndedPayload(live_session_id=other_pk, empty_discarded=True), "test"
    )
    assert not disabled._bg_tasks
    llm.generate.assert_not_awaited()
