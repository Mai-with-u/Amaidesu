"""主播记忆测试：场次回顾 / 观众画像 / 记忆工具 / StreamerAgent 接线。

真库（临时 SQLite）+ mock LLM：
- ``SessionRecapWriter``：原料来自库表；空场次与未结账不写；LLM 失败不写；
  已有回顾不重写；启动补写待办只列已结束且没整理完的；注入渲染排除本场、只取同类型场次
- ``RegularsRosterWriter``：互动门槛、每场人数上限、只取上次整理后的新话（同场重复
  收尾不空转）、LLM 失败逐人降级、观众真假与场次类型相符；付费明细原料行
- ``LongMemoryWriter``：旧笔记 + 本场回顾 + 备忘录合并、按场次类型选文件、没有回顾不动、
  超上限压缩一次仍超就保留旧版本、模型失败保留旧版本、每场只合并一次
- ``build_memory_tool_provider``：往前第 n 场、排除本场、只数同类型场次、按昵称查印象
- ``StreamerAgent``：两仓储齐全才注册记忆工具；开播按场次类型装入上场回顾与长期记忆；
  收播写回顾、整理长期记忆并更新观众画像后记整理完成；停止打断的整理下次启动从断点补完
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
    LongMemoryWriter,
    RegularsRosterWriter,
    SessionRecapWriter,
    build_memory_tool_provider,
    collect_payment_material,
)
from src.agents.streamer.memory.recap import clip_whole_sentences
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.llm.payload import Response
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.chat import GiftRecord, GuardRecord, SuperChatRecord
from src.modules.tools import ToolInvocation, ToolRegistry
from tests.agents.streamer.helpers import ActiveSessionStub
from tests.modules.llm.fakes import FakeLLMManager
from tests.modules.storage.viewer_seed import seed_message


# 登记（驱动/只读穿透，合并时进底账）：直接驱动生命周期处理器与后台善后
# （agent._register_tools / _on_live_started / _on_live_ended / _bootstrap_recaps），
# 读取上场回顾段（agent._session_recap_text）、长期记忆槽位（agent._scope.long_memory）
# 与记忆整理任务集合（agent._memory_tasks）；
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


def _responses(*texts: str) -> MagicMock:
    """按顺序回放多条成功回复的 LLM 替身（长期记忆压缩重试要两次调用）。"""
    llm = MagicMock()
    replies = []
    for text in texts:
        response = MagicMock()
        response.ok, response.content = True, text
        replies.append(response)
    llm.generate = AsyncMock(side_effect=replies)
    return llm


def _long_writer(
    store: SQLiteDatabase, llm: MagicMock, *, notebook: str = "", max_chars: int = 300
) -> LongMemoryWriter:
    async def notebook_of(_live_session_id: int) -> str:
        return notebook

    return LongMemoryWriter(
        store=store.long_memory,
        sessions_repo=store.sessions,
        recap_repo=store.recaps,
        llm_service=llm,
        prompt_manager=_prompts(),
        notebook_text_provider=notebook_of,
        max_chars=max_chars,
    )


async def _recapped_session(store: SQLiteDatabase, recap: str, *, kind: str = "live") -> int:
    pk = await _ended_session(store, kind=kind)
    await store.recaps.upsert_recap(live_session_id=pk, started_at_ms=1_000, ended_at_ms=9_000, recap_text=recap)
    return pk


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
async def test_recap_material_samples_whole_session_and_keeps_notebook(store: SQLiteDatabase) -> None:
    """长场次的发言按全场均匀取样（开头答应的事也在）；整体超长时抽稀发言，备忘录排最前、一字不少。"""
    pk = await _ended_session(store, started=0, ended=3 * 3_600_000)
    await _streamer_says(store, pk, "开场就答应老王下场挖钻石", 1_000)
    for i in range(1, 300):
        await _streamer_says(store, pk, f"第{i}句闲聊" + "嗯" * 110, 1_000 + i * 30_000)
    await _streamer_says(store, pk, "最后一句：下播啦", 9_000_000)

    async def notebook_of(_live_session_id: int) -> str:
        return "答应老王挖钻石；床名定成咕咕床"

    writer = SessionRecapWriter(
        sessions_repo=store.sessions,
        chat_repo=store.chat,
        recap_repo=store.recaps,
        llm_service=_llm(),
        prompt_manager=_prompts(),
        notebook_text_provider=notebook_of,
    )
    material = await writer.collect_material(pk)

    assert material.index("【主播备忘录") < material.index("【主播自己说过的话")
    assert "答应老王挖钻石；床名定成咕咕床" in material
    assert "开场就答应老王下场挖钻石" in material and "最后一句：下播啦" in material
    assert len(material) <= 12_000
    assert material.count(" 主播: ") <= 80


@pytest.mark.asyncio
async def test_recap_material_lists_viewer_corrections_with_prior_line(store: SQLiteDatabase) -> None:
    """观众指出主播记错时，原料里单列这条弹幕和主播前一句；"不对劲"这类不算纠错。"""
    pk = await _ended_session(store)
    await _streamer_says(store, pk, "上次是老王送的舰长", 2_000)
    await _viewer_says(store, pk, "u1", "老李", "你记错了，舰长是我送的", 2_500)
    await _viewer_says(store, pk, "u2", "路人", "这地形有点不对劲", 3_000)

    material = await _recap_writer(store, _llm()).collect_material(pk)

    section = material.split("【观众指出主播说错、记错的地方（连同主播前一句）】\n", 1)[1]
    assert "老李: 你记错了，舰长是我送的（前一句主播说：上次是老王送的舰长）" in section
    assert "不对劲" not in section


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("短句。", 10, "短句。"),
        ("第一句话。第二句话很长很长。第三句", 14, "第一句话。第二句话很长很长。"),
        ("一句没有标点的很长很长的话", 8, "一句没有标点的很"),
        ("短。" + "长" * 30, 20, "短。" + "长" * 18),
    ],
    ids=["fits", "cut_after_sentence", "no_sentence_end", "sentence_end_too_early"],
)
def test_clip_whole_sentences(text: str, limit: int, expected: str) -> None:
    """截短停在完整句子之后；最后一个句末标点不到上限一半时按字截。"""
    assert clip_whole_sentences(text, limit) == expected


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
    assert await _recap_writer(store, _llm("补写的回顾"), bus).ensure_recap(backfill_pk) is True
    assert bus.emit.await_args.args[1].live_session_id == backfill_pk
    assert await store.recaps.get_recap(live_session_id=backfill_pk) is not None

    # 回顾已在库里（整理在回顾写完后被打断）：不重写、不调模型、不再发通知
    bus.emit = AsyncMock()
    llm = _llm("不该重写")
    assert await _recap_writer(store, llm, bus).ensure_recap(backfill_pk) is True
    llm.generate.assert_not_awaited()
    bus.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_recap_unsettled_list_and_render_recent(store: SQLiteDatabase) -> None:
    """启动补写待办按结束时刻取最近一场没整理完的；注入渲染最近一场在前、排除本场。"""
    old_pk = await _ended_session(store, started=1_000, ended=2_000)
    new_pk = await _ended_session(store, started=3_000, ended=4_000)
    for pk in (old_pk, new_pk):
        await _streamer_says(store, pk, f"第{pk}场的话", 1_500)
    writer = _recap_writer(store, _llm("补写的回顾"))

    assert await writer.list_unsettled(limit=1) == [new_pk]
    assert await writer.ensure_recap(new_pk) is True
    assert await store.recaps.get_recap(live_session_id=new_pk) is not None
    assert await store.recaps.get_recap(live_session_id=old_pk) is None
    # 写了回顾但没记完成，仍是待办；记完成后才轮到更早那场
    assert await writer.list_unsettled(limit=1) == [new_pk]
    assert await writer.mark_settled(new_pk) is True
    assert await writer.list_unsettled(limit=1) == [old_pk]
    assert await writer.mark_settled(old_pk) is False  # 没有回顾记不上完成

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
# 长期记忆
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_memory_merges_recap_and_notebook_into_same_kind_file(store: SQLiteDatabase) -> None:
    """原料 = 旧笔记 + 本场回顾 + 备忘录；正式场写正式那份，彩排场写彩排那份，互不改写。"""
    await store.long_memory.write("live", "## 老梗与口头禅\n- 床名一直没定")
    live_pk = await _recapped_session(store, "这场我答应老王下场去挖钻石")
    llm = _responses("## 答应过的事\n- 我答应老王挖钻石（未兑现）")

    text = await _long_writer(store, llm, notebook="床名定成咕咕床").consolidate(live_pk)

    assert text == "## 答应过的事\n- 我答应老王挖钻石（未兑现）"
    assert await store.long_memory.read("live") == text
    prompt = llm.generate.await_args.args[0]
    for marker in ("床名一直没定", "这场我答应老王下场去挖钻石", "床名定成咕咕床"):
        assert marker in prompt
    assert await store.long_memory.read("rehearsal") == ""

    rehearsal_pk = await _recapped_session(store, "调试时说要挖钻石", kind="rehearsal")
    await _long_writer(store, _responses("彩排里记的")).consolidate(rehearsal_pk)
    assert await store.long_memory.read("rehearsal") == "彩排里记的"
    assert await store.long_memory.read("live") == text


@pytest.mark.asyncio
async def test_long_memory_untouched_without_recap_or_on_llm_failure(store: SQLiteDatabase) -> None:
    await store.long_memory.write("live", "旧笔记")
    no_recap_pk = await _ended_session(store)
    llm = _responses("不该写")
    assert await _long_writer(store, llm).consolidate(no_recap_pk) is None
    llm.generate.assert_not_awaited()

    pk = await _recapped_session(store, "这场挖了矿")
    assert await _long_writer(store, _llm(success=False)).consolidate(pk) is None
    assert await _long_writer(store, _llm(success=False)).consolidate(pk + 999) is None  # 场次不存在
    assert await store.long_memory.read("live") == "旧笔记"


@pytest.mark.asyncio
async def test_long_memory_over_limit_compresses_once_then_keeps_old(store: SQLiteDatabase) -> None:
    """超上限让模型压缩一次；压缩后合规就写，仍超出就保留旧版本，不截成半句。"""
    await store.long_memory.write("live", "旧笔记")
    pk = await _recapped_session(store, "这场聊了很多")

    llm = _responses("长" * 400, "压缩后的笔记")
    assert await _long_writer(store, llm, max_chars=300).consolidate(pk) == "压缩后的笔记"
    assert llm.generate.await_count == 2
    assert "超过了 300 字的上限" in llm.generate.await_args_list[1].args[0]

    stubborn = _responses("长" * 400, "还是" * 200)
    assert await _long_writer(store, stubborn, max_chars=300).consolidate(pk) is None
    assert await store.long_memory.read("live") == "压缩后的笔记"


@pytest.mark.asyncio
async def test_long_memory_merges_each_session_once(store: SQLiteDatabase) -> None:
    """合并成功（含内容无变化）就在回顾行记合并时刻，再整理同一场不调模型、不改笔记；失败不记。"""
    pk = await _recapped_session(store, "这场答应老王挖钻石")
    assert await _long_writer(store, _llm(success=False)).consolidate(pk) is None
    recap = await store.recaps.get_recap(live_session_id=pk)
    assert recap is not None and recap.long_memory_merged_at_ms is None  # 失败不记，留给下次

    assert await _long_writer(store, _responses("第一次合并")).consolidate(pk) == "第一次合并"
    recap = await store.recaps.get_recap(live_session_id=pk)
    assert recap is not None and recap.long_memory_merged_at_ms is not None

    again = _responses("不该再并")
    assert await _long_writer(store, again).consolidate(pk) is None
    again.generate.assert_not_awaited()
    assert await store.long_memory.read("live") == "第一次合并"

    unchanged_pk = await _recapped_session(store, "这场没新东西")
    assert await _long_writer(store, _responses("第一次合并")).consolidate(unchanged_pk) == "第一次合并"
    unchanged = await store.recaps.get_recap(live_session_id=unchanged_pk)
    assert unchanged is not None and unchanged.long_memory_merged_at_ms is not None


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
    store: SQLiteDatabase | None,
    llm: Any,
    registry: ToolRegistry | None = None,
    *,
    session_manager: Any = None,
    **memory: Any,
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
            "long_memory_store": store.long_memory,
        }
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=_prompts(),
        tool_registry=registry,
        session_manager=session_manager,
        **repos,
    )


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
async def test_live_started_loads_long_memory_of_same_kind(store: SQLiteDatabase) -> None:
    """开播按场次类型读出整份长期记忆装进槽位（决策与快嘴两层都读它）；关掉开关就不装。"""
    await store.long_memory.write("live", "正式线的笔记")
    await store.long_memory.write("rehearsal", "彩排线的笔记")

    agent = _build_agent(store, _llm())
    await agent._on_live_started("live.started", LiveStartedPayload(live_session_id=9), "test")
    assert agent._scope.long_memory == "正式线的笔记"
    await agent._on_live_started("live.started", LiveStartedPayload(live_session_id=10, kind="rehearsal"), "test")
    assert agent._scope.long_memory == "彩排线的笔记"

    disabled = _build_agent(store, _llm(), long_memory_enabled=False)
    await disabled._on_live_started("live.started", LiveStartedPayload(live_session_id=9), "test")
    assert disabled._scope.long_memory == ""


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
    assert await store.long_memory.read("live") == "补写结果"  # 补写的场次也合并进长期记忆
    assert agent._scope.long_memory == "补写结果"
    assert await store.recaps.list_unsettled_sessions(limit=5) == []  # 补完记整理完成


@pytest.mark.asyncio
async def test_live_ended_settles_recap_and_roster(store: SQLiteDatabase) -> None:
    """收播：后台写本场回顾 + 更新常客；空场次与关闭开关时不整理。"""
    pk = await _ended_session(store)
    await _streamer_says(store, pk, "下场继续挖", 2_000)
    for i in range(3):
        await _viewer_says(store, pk, "u1", "老王", f"第{i}句", 3_000 + i)
    agent = _build_agent(store, _llm("整理结果"))

    await agent._on_live_ended("live.ended", LiveEndedPayload(live_session_id=pk), "test")
    await asyncio.gather(*agent._memory_tasks)

    recap = await store.recaps.get_recap(live_session_id=pk)
    assert recap is not None and recap.settled_at_ms is not None  # 三步走完记整理完成
    assert await _profile_text(store, "u1") == "整理结果"
    assert await store.long_memory.read("live") == "整理结果"  # 回顾之后合并进长期记忆

    other_pk = await _ended_session(store)
    await _streamer_says(store, other_pk, "又一场", 2_000)
    llm = _llm("不该写")
    disabled = _build_agent(store, llm, recap_enabled=False, roster_enabled=False, long_memory_enabled=False)
    await disabled._on_live_ended("live.ended", LiveEndedPayload(live_session_id=other_pk), "test")
    await disabled._on_live_ended(
        "live.ended", LiveEndedPayload(live_session_id=other_pk, empty_discarded=True), "test"
    )
    assert not disabled._memory_tasks
    llm.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_notebook_written_live_feeds_settle_after_app_stop(store: SQLiteDatabase) -> None:
    """直播中写的备忘录随写入落进场次行：应用停机后下次启动补整理，回顾原料里仍有它。"""
    pk = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=1_000)
    await _streamer_says(store, pk, "下场继续挖", 2_000)
    pointer = ActiveSessionStub(pk)
    registry = ToolRegistry()
    agent = _build_agent(store, _llm(), registry, session_manager=pointer)
    agent._register_tools()
    await registry.invoke(ToolInvocation(tool_name="streamer_notebook", arguments={"content": "答应老王挖钻石"}))

    # 应用停机：场次结账，Agent 已停、收播整理留给下次启动
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=9_000)
    pointer.active_pk = None
    llm = _llm("补写的回顾")
    await _build_agent(store, llm)._bootstrap_recaps()

    recap_prompt = next(
        call.args[0] for call in llm.generate.await_args_list if call.args[0].startswith("以下是这一场直播留下的记录")
    )
    assert "答应老王挖钻石" in recap_prompt


@pytest.mark.asyncio
async def test_stop_cancels_memory_task_stuck_on_llm(store: SQLiteDatabase) -> None:
    """停止时记忆整理正停在 LLM 调用上：直接取消，停止返回后不再调模型、回顾不落库。"""
    pk = await _ended_session(store)
    await _streamer_says(store, pk, "下场继续挖", 2_000)
    gate = asyncio.Event()
    llm = FakeLLMManager(gate=gate)
    agent = _build_agent(store, llm)
    # 启动善后补写这场漏掉的回顾，补写停在 LLM 调用上
    await agent.start()
    await llm.gate_reached.wait()
    [backfill] = agent._memory_tasks

    await agent.stop()

    assert backfill.cancelled() and not agent._memory_tasks
    assert llm.calls == []
    assert await store.recaps.get_recap(live_session_id=pk) is None


@pytest.mark.asyncio
async def test_live_ended_after_stop_leaves_settle_to_next_boot(store: SQLiteDatabase) -> None:
    """应用停机路径（Agent 先停、live.ended 后到）不起收播整理，下次启动的补写把这场补上。"""
    llm = FakeLLMManager()
    agent = _build_agent(store, llm)
    await agent.start()
    await asyncio.gather(*agent._memory_tasks)  # 启动善后无场次可补，空跑结束
    await agent.stop()

    pk = await _ended_session(store)
    await _streamer_says(store, pk, "下场继续挖", 2_000)
    await agent._on_live_ended("live.ended", LiveEndedPayload(live_session_id=pk, reason="进程退出"), "test")

    assert not agent._memory_tasks and llm.calls == []
    assert await store.recaps.get_recap(live_session_id=pk) is None

    rebooted = _build_agent(store, FakeLLMManager())
    await rebooted._bootstrap_recaps()
    assert await store.recaps.get_recap(live_session_id=pk) is not None


def _settle_step(call: dict[str, Any]) -> str:
    """按提示词认出一次 LLM 调用属于收播整理的哪一步。"""
    prompt = str(call["input"])
    if prompt.startswith("以下是这一场直播留下的记录"):
        return "回顾"
    if prompt.startswith("【现在的长期笔记】"):
        return "长期记忆"
    if prompt.startswith("观众："):
        return "画像"
    return "其他"


async def _release_one(llm: FakeLLMManager, gate: asyncio.Event) -> None:
    """放行停在闸门前的那一次调用，再等下一次调用停到闸门前（闸门随即关回）。"""
    llm.gate_reached.clear()
    gate.set()
    gate.clear()
    await llm.gate_reached.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finished_steps", "resumed_steps"),
    [(1, ["长期记忆", "画像"]), (2, ["画像"])],
    ids=["stalled_at_long_memory", "stalled_at_roster"],
)
async def test_stop_mid_settle_resumes_remaining_steps_on_next_boot(
    store: SQLiteDatabase, finished_steps: int, resumed_steps: list[str]
) -> None:
    """回顾写完后整理被停止打断：这场留在待办里，下次启动只补没做完的步骤，回顾不重写、长期记忆不重并。"""
    pk = await _ended_session(store)
    await _streamer_says(store, pk, "下场继续挖", 2_000)
    for i in range(3):
        await _viewer_says(store, pk, "u1", "老王", f"第{i}句", 3_000 + i)
    gate = asyncio.Event()
    llm = FakeLLMManager(Response(success=True, content="停机前写的"), gate=gate)
    agent = _build_agent(store, llm)
    # 启动善后补写这场：放行前几步，停在下一步的 LLM 调用上
    await agent.start()
    await llm.gate_reached.wait()
    for _ in range(finished_steps):
        await _release_one(llm, gate)

    await agent.stop()

    recap = await store.recaps.get_recap(live_session_id=pk)
    assert recap is not None and recap.recap_text == "停机前写的" and recap.settled_at_ms is None
    assert (recap.long_memory_merged_at_ms is not None) == (finished_steps >= 2)
    assert [row.live_session_id for row in await store.recaps.list_unsettled_sessions(limit=5)] == [pk]

    rebooted_llm = FakeLLMManager(Response(success=True, content="重启后补的"))
    rebooted = _build_agent(store, rebooted_llm)
    await rebooted._bootstrap_recaps()

    assert [_settle_step(call) for call in rebooted_llm.calls] == resumed_steps
    recap = await store.recaps.get_recap(live_session_id=pk)
    assert recap is not None and recap.recap_text == "停机前写的" and recap.settled_at_ms is not None
    expected_memory = "停机前写的" if finished_steps >= 2 else "重启后补的"
    assert await store.long_memory.read("live") == expected_memory
    assert await _profile_text(store, "u1") == "重启后补的"

    # 整理已记完成：再启动一次没有待办，不调模型
    rebooted_llm.calls.clear()
    await rebooted._bootstrap_recaps()
    assert rebooted_llm.calls == []


@pytest.mark.asyncio
async def test_settle_without_recap_stays_pending(store: SQLiteDatabase) -> None:
    """回顾没写成（模型失败）的场次记不上整理完成，下次启动仍会再试回顾。"""
    pk = await _ended_session(store)
    await _streamer_says(store, pk, "下场继续挖", 2_000)
    agent = _build_agent(store, FakeLLMManager(fail=True))

    await agent._settle_session(pk)

    assert await store.recaps.get_recap(live_session_id=pk) is None
    assert [row.live_session_id for row in await store.recaps.list_unsettled_sessions(limit=5)] == [pk]
