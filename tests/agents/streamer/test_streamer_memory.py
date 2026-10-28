"""主播记忆测试：场次回顾 / 观众画像 / 记忆工具 / StreamerAgent 接线。

真库（临时 SQLite）+ mock LLM：
- ``SessionRecapWriter``：原料来自库表；空场次与未结账不写；LLM 失败不写；
  启动补写只补已结束且缺回顾的；注入渲染排除本场、只取同类型场次
- ``RegularsRosterWriter``：互动门槛、每场人数上限、只取上次整理后的新话（同场重复
  收尾不空转）、LLM 失败逐人降级、观众真假与场次类型相符；付费明细原料行
- ``build_memory_tool_provider``：往前第 n 场、排除本场、只数同类型场次、按昵称查印象
- ``StreamerAgent``：两仓储齐全才注册记忆工具；开播按场次类型装入上场回顾；收播写回顾并更新观众画像
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
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.chat import GiftRecord, GuardRecord, SuperChatRecord
from src.modules.tools import ToolInvocation, ToolRegistry
from tests.modules.storage.viewer_seed import seed_message


# 登记（驱动/只读穿透，合并时进底账）：直接驱动生命周期处理器与后台善后
# （agent._register_tools / _on_live_started / _on_live_ended / _bootstrap_recaps），
# 读取上场回顾段（agent._session_recap_text）与后台任务集合（agent._bg_tasks）；
# 均无公开观测面。


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


async def _viewer_says(
    store: SQLiteDatabase, pk: int, user_id: str, name: str, text: str, ts: int, *, simulated: bool = False
) -> int:
    """造一条观众弹幕：先归到观众并计数，再落带观众 ID 的 live_chat 行（与记账器同效果），返回观众 ID。"""
    viewer_id = await seed_message(
        store.viewers, platform="bilibili", user_id=user_id, user_name=name, timestamp_ms=ts, simulated=simulated
    )
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
        viewer_id=viewer_id,
        id_kind="account",
        simulated=simulated,
    )
    return viewer_id


async def _profile_text(store: SQLiteDatabase, user_id: str) -> Any:
    """按外部 ID 找到观众再取画像；观众没建档时返回 None。"""
    viewer_id = await store.viewers.find_viewer_id(platform="bilibili", external_id=user_id)
    return await store.viewer_profiles.get_profile_text(viewer_id=viewer_id) if viewer_id is not None else None


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


async def _ended_session(store: SQLiteDatabase, *, started: int = 1_000, ended: int = 9_000, kind: str = "live") -> int:
    pk = await store.sessions.insert_live_session(
        stream_id="room", platform="bilibili", started_at_ms=started, kind=kind
    )
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=ended)
    return pk


def _recap_writer(store: SQLiteDatabase, llm: MagicMock, event_bus: Any = None) -> SessionRecapWriter:
    return SessionRecapWriter(
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        recap_repo=store.recaps,
        topic_repo=store.topics,
        event_repo=store.events,
        llm_service=llm,
        prompt_manager=_prompts(),
        max_length=200,
        event_bus=event_bus,
    )


def _roster_writer(store: SQLiteDatabase, llm: MagicMock, **kwargs: Any) -> RegularsRosterWriter:
    return RegularsRosterWriter(
        sessions_repo=store.sessions,
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
    assert row is not None and row.recap_text == text


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
async def test_recap_written_event_on_settle_and_backfill_only_when_stored(store: SQLiteDatabase) -> None:
    """回顾落库后发 streamer.recap.written（收播与补写同一出口）；没写成不发，发布失败不推翻落库。"""
    bus = MagicMock()
    bus.emit = AsyncMock()
    pk = await _ended_session(store, started=1_000, ended=9_000)
    await _streamer_says(store, pk, "答应下场挖钻石", 2_000)

    text = await _recap_writer(store, _llm("这场答应了挖钻石"), bus).write_recap(pk)

    event_name, payload = bus.emit.await_args.args[:2]
    assert event_name == CoreEvents.STREAMER_RECAP_WRITTEN
    assert (payload.live_session_id, payload.started_at_ms, payload.ended_at_ms) == (pk, 1_000, 9_000)
    assert payload.recap_text == text
    assert payload.kind == "live"

    # 彩排场的回顾照写（彩排线自己要用），事件带上类型让对外同步据此跳过
    rehearsal_pk = await _ended_session(store, kind="rehearsal")
    await _streamer_says(store, rehearsal_pk, "调试时说的话", 2_000)
    assert await _recap_writer(store, _llm("彩排回顾"), bus).write_recap(rehearsal_pk)
    assert bus.emit.await_args.args[1].kind == "rehearsal"

    bus.emit.reset_mock()
    failed_pk = await _ended_session(store)
    await _streamer_says(store, failed_pk, "说了点什么", 2_000)
    assert await _recap_writer(store, _llm(success=False), bus).write_recap(failed_pk) is None
    bus.emit.assert_not_awaited()

    backfill_pk = await _ended_session(store, started=20_000, ended=30_000)
    await _streamer_says(store, backfill_pk, "上次没来得及总结", 21_000)
    bus.emit = AsyncMock(side_effect=RuntimeError("总线已关"))
    assert await _recap_writer(store, _llm("补写的回顾"), bus).backfill_missing(limit=1) == [backfill_pk]
    assert bus.emit.await_args.args[1].live_session_id == backfill_pk
    assert await store.recaps.get_recap(live_session_id=backfill_pk) is not None


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

    rendered = await writer.render_recent(count=1, kind="live")
    assert rendered.startswith("【") and "补写的回顾" in rendered
    assert await writer.render_recent(count=1, kind="live", exclude_session_id=new_pk) == ""
    assert await writer.render_recent(count=0, kind="live") == ""


@pytest.mark.asyncio
async def test_render_recent_reads_same_kind_only(store: SQLiteDatabase) -> None:
    """正式场只接正式场的回顾，彩排场只接彩排场的：昨晚的调试场不是今晚正式直播的上一场。"""
    live_pk = await _ended_session(store, started=1_000, ended=2_000)
    rehearsal_pk = await _ended_session(store, started=3_000, ended=4_000, kind="rehearsal")
    await store.recaps.upsert_recap(
        live_session_id=live_pk, started_at_ms=1_000, ended_at_ms=2_000, recap_text="正式场"
    )
    await store.recaps.upsert_recap(
        live_session_id=rehearsal_pk, started_at_ms=3_000, ended_at_ms=4_000, recap_text="调试场"
    )
    writer = _recap_writer(store, _llm("不会调用"))

    live_text = await writer.render_recent(count=3, kind="live")
    assert "正式场" in live_text and "调试场" not in live_text
    rehearsal_text = await writer.render_recent(count=3, kind="rehearsal")
    assert "调试场" in rehearsal_text and "正式场" not in rehearsal_text


# ---------------------------------------------------------------------------
# 观众画像
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
    assert await _profile_text(store, "u_new") is None

    writer = _roster_writer(store, llm, min_interactions=3, batch_max=5)
    assert await writer.update_for_session(pk) == 1  # 已更新的那位上次整理后没新话，只剩另一位
    assert await writer.update_for_session(pk) == 0  # 同场重复收尾不空转
    assert llm.generate.await_count == 2
    assert await store.viewer_profiles.count_profiles() == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "updated_user"), [("live", "u_real"), ("rehearsal", "u_sim")])
async def test_roster_only_updates_viewers_matching_session_kind(
    store: SQLiteDatabase, kind: str, updated_user: str
) -> None:
    """正式场只更新真实观众，彩排场只更新模拟观众；场次删掉了就不更新。"""
    pk = await _ended_session(store, kind=kind)
    for i in range(3):
        await _viewer_says(store, pk, "u_real", "真观众", f"真的第{i}句", 1_000 + i)
        await _viewer_says(store, pk, "u_sim", "模拟观众", f"假的第{i}句", 2_000 + i, simulated=True)
    writer = _roster_writer(store, _llm("印象"), min_interactions=3)

    assert await writer.update_for_session(pk) == 1
    skipped_user = "u_sim" if updated_user == "u_real" else "u_real"
    assert await _profile_text(store, updated_user) == "印象"
    assert await _profile_text(store, skipped_user) is None

    await store.sessions.delete_live_session(live_session_id=pk)
    assert await writer.update_for_session(pk) == 0


@pytest.mark.asyncio
async def test_roster_llm_failure_leaves_profile_absent(store: SQLiteDatabase) -> None:
    pk = await _ended_session(store)
    for i in range(3):
        await _viewer_says(store, pk, "u1", "老王", f"第{i}句", 1_000 + i)
    assert await _roster_writer(store, _llm(success=False)).update_for_session(pk) == 0
    assert await _profile_text(store, "u1") is None


@pytest.mark.asyncio
async def test_payment_material_lines() -> None:
    """原料行含付费汇总、三类明细与最近一次付费时的身份快照；仓储缺失降级为空。"""
    chat_repo = MagicMock()
    chat_repo.summarize_viewer_contributions = AsyncMock(
        return_value={"gift_total_amount": 2000, "sc_total_amount": 50_000}
    )
    # 明细行用真实契约模型构造（required 字段补最小占位）
    medal = {"fans_medal_level": 21, "fans_medal_name": "粉丝团"}
    chat_repo.list_viewer_gifts = AsyncMock(
        return_value=[
            GiftRecord(
                id=1,
                live_session_id=1,
                timestamp_ms=100,
                user_id="u",
                user_name="观众",
                gift_name="小星星",
                quantity=3,
                guard_level=3,
                **medal,
            )
        ]
    )
    chat_repo.list_viewer_super_chats = AsyncMock(
        return_value=[
            SuperChatRecord(
                id=1,
                live_session_id=1,
                timestamp_ms=200,
                user_id="u",
                user_name="观众",
                message="加油",
                guard_level=0,
                **medal,
            )
        ]
    )
    chat_repo.list_viewer_guards = AsyncMock(
        return_value=[
            GuardRecord(
                id=1,
                live_session_id=1,
                timestamp_ms=300,
                user_id="u",
                user_name="观众",
                guard_level=3,
                guard_num=1,
                guard_unit="月",
                **medal,
            )
        ]
    )

    joined = "\n".join(await collect_payment_material(chat_repo, 1))

    assert "累计付费约 52 元" in joined
    assert "送出礼物 小星星×3" in joined and "发送 SC「加油」" in joined and "开通舰长（1月）" in joined
    assert "最近一次付费时的身份：舰长、粉丝团21 级牌" in joined
    assert await collect_payment_material(None, 1) == []


# ---------------------------------------------------------------------------
# 记忆工具
# ---------------------------------------------------------------------------


async def _invoke(provider: Any, name: str, **arguments: Any) -> str:
    result = await provider.invoke(ToolInvocation(tool_name=name, arguments=arguments))
    assert result.success, result.error_message
    return result.content


@pytest.mark.asyncio
async def test_recall_session_tool(store: SQLiteDatabase) -> None:
    pks = []
    for started in (1_000, 5_000, 9_000):
        sid = await _ended_session(store, started=started, ended=started + 60_000)
        await store.recaps.upsert_recap(
            live_session_id=sid, started_at_ms=started, ended_at_ms=started + 60_000, recap_text=f"第{sid}场回顾"
        )
        pks.append(sid)
    assert pks == [1, 2, 3]
    rehearsal_pk = await _ended_session(store, started=20_000, ended=30_000, kind="rehearsal")
    await store.recaps.upsert_recap(
        live_session_id=rehearsal_pk, started_at_ms=20_000, ended_at_ms=30_000, recap_text="彩排回顾"
    )
    current = {"pk": 3, "kind": "live"}
    provider = build_memory_tool_provider(
        recap_repo=store.recaps,
        profile_repo=store.viewer_profiles,
        current_session_provider=lambda: current["pk"],
        current_kind_provider=lambda: current["kind"],
    )
    assert {spec.full_name for spec in provider.list_tools()} == {"memory_recall_session", "memory_recall_viewer"}

    assert "第2场回顾" in await _invoke(provider, "memory_recall_session")  # 本场（3）排除
    assert "第1场回顾" in await _invoke(provider, "memory_recall_session", n_back=2)
    assert "没有那么早" in await _invoke(provider, "memory_recall_session", n_back=5)

    current["pk"] = None  # 未开播：上一场就是最近一场正式场（更晚的彩排场不算）
    assert "第3场回顾" in await _invoke(provider, "memory_recall_session")

    current["kind"] = "rehearsal"  # 彩排里往前数只数彩排场
    assert "彩排回顾" in await _invoke(provider, "memory_recall_session")
    assert "没有那么早" in await _invoke(provider, "memory_recall_session", n_back=2)


@pytest.mark.asyncio
async def test_recall_viewer_tool(store: SQLiteDatabase) -> None:
    vid = await seed_message(store.viewers, platform="bilibili", user_id="u1", user_name="老王", timestamp_ms=1)
    await store.viewer_profiles.upsert_profile(viewer_id=vid, profile_text="老粉，爱看挖矿", last_compressed_at_ms=1)
    provider = build_memory_tool_provider(
        recap_repo=store.recaps, profile_repo=store.viewer_profiles, viewer_repo=store.viewers
    )

    assert "老粉，爱看挖矿" in await _invoke(provider, "memory_recall_viewer", nickname="老王")
    assert "老粉，爱看挖矿" in await _invoke(provider, "memory_recall_viewer", viewer_id=vid)
    assert "没找到" in await _invoke(provider, "memory_recall_viewer", nickname="路人")
    # 改名后用以前的名字也能找到，并提醒主播对方现在叫什么
    await seed_message(store.viewers, platform="bilibili", user_id="u1", user_name="王老", timestamp_ms=2)
    by_old_name = await _invoke(provider, "memory_recall_viewer", nickname="老王")
    assert "老粉，爱看挖矿" in by_old_name and "现在叫「王老」" in by_old_name
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
    """开播：读出最近一场同类型回顾（排除本场）装进参考段；关注入时为空。"""
    live_pk = await _ended_session(store, started=1_000, ended=2_000)
    rehearsal_pk = await _ended_session(store, started=3_000, ended=4_000, kind="rehearsal")
    await store.recaps.upsert_recap(
        live_session_id=live_pk, started_at_ms=1_000, ended_at_ms=2_000, recap_text="上场挖到钻石"
    )
    await store.recaps.upsert_recap(
        live_session_id=rehearsal_pk, started_at_ms=3_000, ended_at_ms=4_000, recap_text="调试时乱说的"
    )
    agent = _build_agent(store, _llm())
    await agent._on_live_started("live.started", LiveStartedPayload(live_session_id=9), "test")
    assert "上场挖到钻石" in agent._session_recap_text()
    assert "调试时乱说的" not in agent._session_recap_text()

    rehearsal = _build_agent(store, _llm())
    await rehearsal._on_live_started("live.started", LiveStartedPayload(live_session_id=9, kind="rehearsal"), "test")
    assert "调试时乱说的" in rehearsal._session_recap_text()
    assert "上场挖到钻石" not in rehearsal._session_recap_text()

    muted = _build_agent(store, _llm(), recap_inject_count=0)
    await muted._on_live_started("live.started", LiveStartedPayload(live_session_id=2), "test")
    assert muted._session_recap_text() == ""


@pytest.mark.asyncio
async def test_bootstrap_backfills_recap_roster_and_section(store: SQLiteDatabase) -> None:
    """启动善后：补写漏掉的回顾、补跑该场画像更新，并把回顾装进上场回顾段。"""
    pk = await _ended_session(store)
    await _streamer_says(store, pk, "下场继续挖", 2_000)
    for i in range(3):
        await _viewer_says(store, pk, "u1", "老王", f"第{i}句", 3_000 + i)
    agent = _build_agent(store, _llm("补写结果"))

    await agent._bootstrap_recaps()

    assert await store.recaps.get_recap(live_session_id=pk) is not None
    assert await _profile_text(store, "u1") == "补写结果"
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
    assert await _profile_text(store, "u1") == "整理结果"

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
