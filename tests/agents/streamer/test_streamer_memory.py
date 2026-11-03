"""主播记忆测试：场次回顾 / 观众画像 / 记忆工具 / StreamerAgent 接线。

真库（临时 SQLite）+ mock LLM：
- ``SessionRecapWriter``：原料来自库表；空场次与未结账不写；LLM 失败不写；
  已有回顾不重写；启动补写待办只列已结束且没整理完的；注入渲染排除本场、只取同类型场次
- ``RegularsRosterWriter``：互动门槛、每场人数上限、只取上次整理后的新话（同场重复
  收尾不空转）、没建档的与印象最久没更新的先来、只付费不说话的也建档、LLM 失败逐人
  降级、观众真假与场次类型相符；付费明细原料行
- ``LongMemoryWriter``：模型只给条目操作、按场次类型选文件、写回留底、没有回顾不动、
  输出读不出或模型失败保留旧版本、合并期间被人改过就在新版本上重做、每场只合并一次；
  笔记与 MaiBot 共享时，QQ 里记下的约定读笔记时并进来、不当成人改的行，写完发布注入视图
- ``build_memory_tool_provider``：往前第 n 场、排除本场、只数同类型场次、按昵称查印象
- ``StreamerAgent``：两仓储齐全才注册记忆工具；开播按场次类型装入上场回顾与长期记忆；
  收播写回顾、整理长期记忆并更新观众画像后记整理完成；停止打断的整理下次启动从断点补完
"""

from __future__ import annotations

import json
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
    collect_payment_material,
)
from src.agents.streamer.memory.long_memory import format_long_memory
from src.agents.streamer.memory.notes import absorb_promises
from src.agents.streamer.memory.recap import clip_whole_sentences
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.names import CoreEvents
from src.modules.maibot_sync import SharedNotesStore
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.long_memory import LongMemoryBackend
from src.modules.storage.models.chat import GiftRecord, GuardRecord, SuperChatRecord
from src.modules.tools import ToolRegistry
from tests.modules.maibot_sync.fakes import FakeNotesRemote
from tests.modules.llm.fakes import FakeLLMManager
from tests.modules.storage.viewer_seed import seed_gift, seed_message


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
    store: SQLiteDatabase,
    llm: MagicMock,
    *,
    notebook: str = "",
    max_chars: int = 300,
    backend: LongMemoryBackend | None = None,
    event_repo: Any = None,
) -> LongMemoryWriter:
    """``backend`` 缺省用库里的本地笔记文件；共享笔记的用例传入共享存储。"""

    async def notebook_of(_live_session_id: int) -> str:
        return notebook

    return LongMemoryWriter(
        store=backend or store.long_memory,
        sessions_repo=store.sessions,
        recap_repo=store.recaps,
        chat_repo=store.chat,
        event_repo=event_repo,
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


def _ops(*ops: dict) -> str:
    """长期记忆整理的模型回复：操作 JSON。"""
    return json.dumps({"ops": list(ops)}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_long_memory_applies_item_ops_with_material_and_history(store: SQLiteDatabase) -> None:
    """模型只给条目操作：原料含编号清单、人设、观众名单、回顾与整理原料；约定挂到名单里的观众；
    没动到的行原样保留；写回留底（来由、场次、改动）；正式与彩排各一份。"""
    await store.long_memory.write("live", "## 老梗与口头禅\n- 床名一直没定〔始于 #1 · 最近 #1〕")
    live_pk = await _recapped_session(store, "这场我答应老王下场去挖钻石")
    viewer_id = await _viewer_says(store, live_pk, "u1", "老王", "下场挖钻石！", 2_000)
    llm = _responses(
        _ops({"op": "add", "section": "答应过的事", "text": "我答应老王下场挖钻石", "viewer_id": viewer_id})
    )

    text = await _long_writer(store, llm, notebook="床名定成咕咕床").consolidate(live_pk)

    assert text is not None and "- 床名一直没定〔始于 #1 · 最近 #1〕" in text
    assert f"- 我答应老王下场挖钻石〔未兑现 · 对 老王#{viewer_id} · 始于 #{live_pk} · 最近 #{live_pk}〕" in text
    prompt = llm.generate.await_args.args[0]
    for marker in ("[1] 〔老梗与口头禅〕床名一直没定", "这场我答应老王下场去挖钻石", "床名定成咕咕床",
                   f"#{viewer_id} 老王", "【主播的人设】"):  # fmt: skip
        assert marker in prompt
    entry = (await store.long_memory.list_history("live"))[0]  # 最新一条是这次合并
    assert (entry.session_id, entry.reason) == (live_pk, f"收播整理（场次 #{live_pk}）")
    assert entry.changes == [f"新增〔答应过的事〕我答应老王下场挖钻石（对 老王#{viewer_id}）"]

    rehearsal_pk = await _recapped_session(store, "调试时说要挖钻石", kind="rehearsal")
    await _long_writer(store, _responses(_ops({"op": "add", "section": "长线目标", "text": "彩排里记的"}))).consolidate(
        rehearsal_pk
    )
    assert "彩排里记的" in await store.long_memory.read("rehearsal")
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
async def test_long_memory_keeps_old_version_when_output_unreadable(store: SQLiteDatabase) -> None:
    """模型没给出操作（写成了散文）：保留旧版本、不记合并时刻，下次还能再并。"""
    await store.long_memory.write("live", "## 老梗与口头禅\n- 嘴硬〔最近 #1〕")
    pk = await _recapped_session(store, "这场聊了很多")

    assert await _long_writer(store, _responses("这场没什么要记的，笔记照旧。")).consolidate(pk) is None
    assert await store.long_memory.read("live") == "## 老梗与口头禅\n- 嘴硬〔最近 #1〕"
    recap = await store.recaps.get_recap(live_session_id=pk)
    assert recap is not None and recap.long_memory_merged_at_ms is None


@pytest.mark.asyncio
async def test_long_memory_redoes_ops_on_version_saved_during_merge(store: SQLiteDatabase) -> None:
    """模型思考期间有人在 WebUI 保存了笔记：在新版本上重做同一批操作，人的改动不被覆盖。"""
    await store.long_memory.write("live", "## 老梗与口头禅\n- 嘴硬〔最近 #1〕")
    pk = await _recapped_session(store, "这场又嘴硬了")
    reply = MagicMock()
    reply.ok, reply.content = True, _ops({"op": "add", "section": "长线目标", "text": "盖海景房"})

    async def human_saves_meanwhile(*_args: Any, **_kwargs: Any) -> Any:
        await store.long_memory.write("live", "## 老梗与口头禅\n- 嘴硬〔最近 #1〕\n- 人刚加的〔人工〕")
        return reply

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=human_saves_meanwhile)
    text = await _long_writer(store, llm).consolidate(pk)

    assert text is not None and "- 人刚加的〔人工〕" in text and "- 盖海景房〔" in text


@pytest.mark.asyncio
async def test_long_memory_marks_lines_edited_by_hand_since_last_merge(store: SQLiteDatabase) -> None:
    """上次合并之后人改过正文的行（WebUI 或编辑器）在下次合并时记成人工：模型再动它不执行，改动清单里写明。"""
    first_pk = await _recapped_session(store, "第一场")
    add = _ops({"op": "add", "section": "老梗与口头禅", "text": "嘴硬"})
    await _long_writer(store, _responses(add)).consolidate(first_pk)
    edited = (await store.long_memory.read("live")).replace("- 嘴硬〔", "- 嘴硬但心软〔")
    await store.long_memory.write("live", edited, reason="WebUI 保存")

    second_pk = await _recapped_session(store, "第二场")
    text = await _long_writer(store, _responses(_ops({"op": "remove", "id": 1, "reason": "过时"}))).consolidate(
        second_pk
    )

    assert text is not None and "- 嘴硬但心软〔人工 · " in text
    changes = (await store.long_memory.list_history("live"))[0].changes
    assert changes[0] == "人改过的行记为人工：嘴硬但心软"
    assert any(change.startswith("没采纳（人工条目只读）") for change in changes)


@pytest.mark.asyncio
async def test_long_memory_shared_with_maibot_absorbs_qq_promises(store: SQLiteDatabase) -> None:
    """与 MaiBot 共享笔记：QQ 里记下的约定在整理读笔记时并进来，模型思考期间才记下的那条
    留到下一次读时再收；并进来的行不当成人改的，模型能把它标成已兑现。每次写完发布的就是
    开播注入的那份视图，发布之后约定才回报已收。"""
    remote = FakeNotesRemote()
    shared = SharedNotesStore(local=store.long_memory, remote=remote, render=format_long_memory, absorb=absorb_promises)
    await store.long_memory.write("live", "## 老梗与口头禅\n- 嘴硬〔始于 #1 · 最近 #1〕")
    first = remote.record("答应群友小明下次直播唱歌")
    first_pk = await _recapped_session(store, "这场又嘴硬了")
    reply = MagicMock()
    reply.ok, reply.content = True, _ops({"op": "add", "section": "长线目标", "text": "盖海景房"})

    async def qq_records_meanwhile(*_args: Any, **_kwargs: Any) -> Any:
        remote.record("答应群友小红一起画画")
        return reply

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=qq_records_meanwhile)
    text = await _long_writer(store, llm, backend=shared).consolidate(first_pk)

    assert text is not None and "- 盖海景房〔" in text and "小红" not in text
    assert f"- 答应群友小明下次直播唱歌〔未兑现 · 来自 QQ群 · 最近 #{first_pk}〕" in text
    published = format_long_memory(text)
    assert remote.published == published and "- 答应群友小明下次直播唱歌（还没兑现，来自 QQ群）" in published
    assert remote.acks == [[first]] and [item.text for item in remote.inbox] == ["答应群友小红一起画画"]

    second_pk = await _recapped_session(store, "第二场和小红画了画")
    done = _ops({"op": "done", "id": 2, "reason": "画完了"})
    text = await _long_writer(store, _responses(done), backend=shared).consolidate(second_pk)

    assert text is not None and f"- 答应群友小红一起画画〔已兑现 · 来自 QQ群 · 最近 #{second_pk}〕" in text
    changes = (await store.long_memory.list_history("live"))[0].changes
    assert not any(change.startswith("人改过的行") for change in changes)
    assert remote.published == format_long_memory(text) and remote.inbox == []
    await shared.close()


@pytest.mark.asyncio
async def test_long_memory_merges_each_session_once(store: SQLiteDatabase) -> None:
    """合并成功（含内容无变化）就在回顾行记合并时刻，再整理同一场不调模型、不改笔记；失败不记。"""
    pk = await _recapped_session(store, "这场答应老王挖钻石")
    assert await _long_writer(store, _llm(success=False)).consolidate(pk) is None
    recap = await store.recaps.get_recap(live_session_id=pk)
    assert recap is not None and recap.long_memory_merged_at_ms is None  # 失败不记，留给下次

    first = await _long_writer(
        store, _responses(_ops({"op": "add", "section": "长线目标", "text": "挖钻石"}))
    ).consolidate(pk)
    assert first is not None and "挖钻石" in first
    recap = await store.recaps.get_recap(live_session_id=pk)
    assert recap is not None and recap.long_memory_merged_at_ms is not None

    again = _responses("不该再并")
    assert await _long_writer(store, again).consolidate(pk) is None
    again.generate.assert_not_awaited()
    assert await store.long_memory.read("live") == first

    unchanged_pk = await _recapped_session(store, "这场没新东西")
    assert await _long_writer(store, _responses(_ops())).consolidate(unchanged_pk) == first
    unchanged = await store.recaps.get_recap(live_session_id=unchanged_pk)
    assert unchanged is not None and unchanged.long_memory_merged_at_ms is not None


@pytest.mark.asyncio
async def test_long_memory_game_items_age_only_in_sessions_that_played_it(store: SQLiteDatabase) -> None:
    """游戏条目只在玩过那个游戏的场次里算"隔了一场"：连播几场杂谈，游戏里的目标留着，别的照常淡出。"""
    game_pk = await _recapped_session(store, "这场在 MC 里开始搭水车")
    await store.events.insert_game_event(
        live_session_id=game_pk, game="minecraft", event_type="milestone", message="开工", timestamp_ms=2_000
    )
    await store.long_memory.write(
        "live", f"## 长线目标\n- 搭水车〔游戏 minecraft · 最近 #{game_pk}〕\n- 攒钱买新麦〔最近 #{game_pk}〕"
    )
    text = None
    for _ in range(3):
        chat_pk = await _recapped_session(store, "这场纯杂谈")
        text = await _long_writer(store, _responses(_ops()), event_repo=store.events).consolidate(chat_pk)

    assert text is not None
    active, shelved = text.split("## 搁置")
    assert "搭水车" in active and "攒钱买新麦" in shelved


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
async def test_roster_rotates_to_viewers_without_or_with_oldest_profile(store: SQLiteDatabase) -> None:
    """每场只更新几位时轮着来：没建档的先于话更多、刚更新过的常客；这场只说了一句的不算候选。"""
    pk = await _ended_session(store)
    for i in range(6):
        talker = await _viewer_says(store, pk, "u_talk", "话痨", f"话痨第{i}句", 1_000 + i)
    for i in range(3):
        await _viewer_says(store, pk, "u_quiet", "老观众", f"老观众第{i}句", 2_000 + i)
    for i in range(3):
        await seed_message(store.viewers, platform="bilibili", user_id="u_once", user_name="路过", timestamp_ms=i)
    await _viewer_says(store, pk, "u_once", "路过", "就说一句", 3_000)
    await store.viewer_profiles.upsert_profile(viewer_id=talker, profile_text="旧印象", last_compressed_at_ms=500)
    llm = _llm("轮到的印象")

    assert await _roster_writer(store, llm, min_interactions=3, batch_max=1).update_for_session(pk) == 1
    assert await _profile_text(store, "u_quiet") == "轮到的印象"
    assert await _profile_text(store, "u_talk") == "旧印象"
    assert await _profile_text(store, "u_once") is None


@pytest.mark.asyncio
async def test_roster_builds_profile_for_paying_viewer_who_never_chats(store: SQLiteDatabase) -> None:
    """只送礼不说话的观众也是候选：原料里写明这场没说话、只有付费。"""
    pk = await _ended_session(store)
    for i in range(3):
        viewer_id = await seed_gift(
            store.viewers, platform="bilibili", user_id="u_gift", user_name="大佬", timestamp_ms=i
        )
    await store.chat.insert_gift(
        live_session_id=pk,
        timestamp_ms=2_000,
        platform="bilibili",
        user_id="u_gift",
        user_name="大佬",
        gift_name="小电视",
        quantity=1,
        total_price=1_245_000,
        currency="gold",
        viewer_id=viewer_id,
        id_kind="account",
    )
    llm = _llm("送礼大佬，不爱说话")

    assert await _roster_writer(store, llm, min_interactions=3).update_for_session(pk) == 1
    assert await _profile_text(store, "u_gift") == "送礼大佬，不爱说话"
    prompt = llm.generate.await_args.args[0]
    assert "（这场没说话，只有付费）" in prompt and "小电视" in prompt


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


def _build_agent(
    store: SQLiteDatabase | None,
    llm: Any,
    tool_registry: ToolRegistry | None = None,
    *,
    session_manager: Any = None,
    **memory: Any,
) -> StreamerAgent:
    """装一个只接账本仓储的主播 Agent（旧三份记忆在本阶段已停写）。"""
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
            "event_repo": store.events,
            "commitments_repo": store.commitments,
            "archive_repo": store.archive,
        }
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=_prompts(),
        tool_registry=tool_registry,
        session_manager=session_manager,
        **repos,
    )


# ---------------------------------------------------------------------------
# 账本接线：回想工具与常驻简报
# ---------------------------------------------------------------------------


async def test_agent_registers_memory_recall_and_retired_tools_stay_gone(store: SQLiteDatabase) -> None:
    """阶段 3b 起只有一个记忆工具 memory_recall；旧的三个工具不再注册。"""
    registry = ToolRegistry()
    agent = _build_agent(store, FakeLLMManager(), tool_registry=registry)
    agent._register_tools()

    names = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
    assert "memory_recall" in names
    assert not names & {"memory_recall_session", "memory_recall_viewer", "streamer_notebook"}


async def test_refresh_brief_reads_previous_archive_and_pending_commitments(store: SQLiteDatabase) -> None:
    """常驻简报取上一场档案与不针对人、不挂游戏的未了结承诺。"""
    previous_pk = await _ended_session(store, started=1_000, ended=2_000)
    await store.archive.insert_line(
        live_session_id=previous_pk,
        text="Minecraft：水车搭好了",
        quote="水车搭好啦",
        evidence_chat_id=1,
    )
    await store.commitments.insert_commitment(what="继续炸 TNT", quote="下次接着炸 TNT")
    # 对某位观众的、挂游戏的都不进常驻简报
    await store.commitments.insert_commitment(what="给 ta 盖房子", viewer_id=9)
    await store.commitments.insert_commitment(what="继续挖矿", game="Minecraft")

    agent = _build_agent(store, FakeLLMManager())
    await agent._refresh_memory_brief()

    brief = agent.memory_brief_text
    assert "上一场：" in brief and "[档#" in brief and "水车搭好啦" in brief
    assert "下次接着炸 TNT" in brief
    assert "盖房子" not in brief and "继续挖矿" not in brief
    assert len(agent.memory_brief_commitment_ids) == 1


async def test_brief_is_empty_without_ledger_material(store: SQLiteDatabase) -> None:
    agent = _build_agent(store, FakeLLMManager())
    await agent._refresh_memory_brief()
    assert agent.memory_brief_text == "" and agent.memory_brief_commitment_ids == ()
