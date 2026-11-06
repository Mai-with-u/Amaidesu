"""回想工具测试：memory_recall 的检索、排序、定位、遮名与异常降级。

真库（临时 SQLite），时间全用固定毫秒戳（HH:MM 只做形状断言，不依赖真实时钟）。
台词、档案、承诺走混合检索（这里不配嵌入服务，只有关键词一路），每个用例先把索引补算好再查。
覆盖：按相关度选出最相关的几条、newest / oldest 两向排序、scope 只查本场、只认已播出台词
（dropped_at_ms 带标记的不返回）、承诺状态与了结原话、不返回对具体观众的承诺与
判误记的承诺、跨场结果遮名而本场不遮、关键词是昵称时忽略并在抬头提示、
空结果与查询异常都给平实说明不抛。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator, Generator, List, Optional

import pytest

from src.agents.streamer.memory.index import MemoryIndexer
from src.agents.streamer.memory.recall import (
    MEMORY_RECALL_PROVIDER_NAME,
    MEMORY_RECALL_TOOL_NAME,
    ORDER_NEWEST,
    ORDER_OLDEST,
    SCOPE_ALL,
    SCOPE_THIS_STREAM,
    MemoryRecallService,
    build_memory_recall_provider,
)
from src.agents.streamer.memory.search import MemorySearch
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import KeptSnapshotLine
from src.modules.tools import ToolInvocation
from src.modules.types.game_save import GameSave
from tests.helpers import FakeClock
from tests.modules.storage.viewer_seed import seed_message, seed_viewer


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="memory-recall-"))
    yield td / "recall.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    db = SQLiteDatabase(temp_db_path)
    await db.initialize()
    yield db
    await db.close()


async def _session(store: SQLiteDatabase, *, started: int, ended: Optional[int] = None) -> int:
    pk = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=started)
    if ended is not None:
        await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=ended)
    return pk


async def _streamer_says(
    store: SQLiteDatabase,
    pk: int,
    text: str,
    ts: int,
    *,
    dropped_at_ms: Optional[int] = None,
) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=ts,
        sender_role="assistant",
        sender_id="麦麦",
        sender_name="麦麦",
        content=text,
        message_type="speak",
        dropped_at_ms=dropped_at_ms,
    )


async def _viewer_says(store: SQLiteDatabase, pk: int, *, user_id: str, name: str, text: str, ts: int) -> int:
    """造一条观众弹幕：先归到观众并计数，再落带观众 ID 的 live_chat 行（与记账器同效果）。"""
    viewer_id = await seed_message(store.viewers, platform="bilibili", user_id=user_id, user_name=name, timestamp_ms=ts)
    return await store.chat.insert_live_chat(
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
    )


async def _index(store: SQLiteDatabase) -> None:
    """把库里该收的行补进检索索引（后台索引任务跑一轮的效果；用例里的时刻都早于假时钟 30 秒以上）。"""
    await MemoryIndexer(repo=store.memory_index, embedding=None, embedding_note="", clock=FakeClock().now_ms).run_once()


async def _service(store: SQLiteDatabase, *, current_session: Optional[int] = None) -> MemoryRecallService:
    """先补算索引，再建一个接了混合检索的回想服务。"""
    await _index(store)
    return MemoryRecallService(
        chat_repo=store.chat,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
        viewer_repo=store.viewers,
        current_session_provider=lambda: current_session,
        clock=FakeClock().now_ms,
        event_repo=store.events,
        search=MemorySearch(repo=store.memory_index, embedding=None),
    )


def _hit_lines(text: str) -> List[str]:
    return [line for line in text.splitlines() if line.startswith("- ")]


# ---------------------------------------------------------------------------
# 检索与排序
# ---------------------------------------------------------------------------


async def test_recall_picks_the_most_relevant_eight_without_requiring_every_keyword(store: SQLiteDatabase) -> None:
    """十句都提到挖矿，只有最早那句同时提到钻石：它最相关，一定在选出的 8 条里；只提钻石的也能查到。"""
    pk = await _session(store, started=1_000)
    await _streamer_says(store, pk, "今天挖矿挖到钻石了", 1_100)
    for minute in range(10):
        await _streamer_says(store, pk, f"第 {minute} 回接着挖矿", 1_200 + minute)
    await _streamer_says(store, pk, "钻石真好看", 1_300)
    service = await _service(store, current_session=pk)

    text = await service.recall("挖矿 钻石", SCOPE_ALL, ORDER_NEWEST)

    assert "【回想】关键词「挖矿 钻石」" in text
    assert text.splitlines()[0].endswith("8 条")
    assert "挖矿挖到钻石了" in text
    assert "钻石真好看" in text, "不要求每个词都出现"


async def test_recall_newest_and_oldest_order(store: SQLiteDatabase) -> None:
    pk = await _session(store, started=1_000)
    await _streamer_says(store, pk, "第一次提挖矿", 1_100)
    await _streamer_says(store, pk, "第二次提挖矿", 1_200)
    service = await _service(store, current_session=pk)

    newest = await service.recall("挖矿", SCOPE_ALL, ORDER_NEWEST)
    oldest = await service.recall("挖矿", SCOPE_ALL, ORDER_OLDEST)
    assert "顺序：最新在前" in newest and "第二次提挖矿" in _hit_lines(newest)[0]
    assert "顺序：最早在前" in oldest and "第一次提挖矿" in _hit_lines(oldest)[0]


async def test_recall_scope_this_stream_only_returns_current_session(store: SQLiteDatabase) -> None:
    first = await _session(store, started=1_000, ended=2_000)
    current = await _session(store, started=3_000)
    await _streamer_says(store, first, "上一场聊到挖矿", 1_100)
    await _streamer_says(store, current, "本场也聊挖矿", 3_100)
    await _viewer_says(store, current, user_id="u1", name="路人甲", text="我问挖矿的事", ts=3_200)
    service = await _service(store, current_session=current)

    this_stream = await service.recall("挖矿", SCOPE_THIS_STREAM, ORDER_NEWEST)
    assert "范围：本场" in this_stream
    assert "本场也聊挖矿" in this_stream
    assert "上一场聊到挖矿" not in this_stream
    assert "我问挖矿的事" in this_stream  # 本场观众弹幕

    everything = await service.recall("挖矿", SCOPE_ALL, ORDER_NEWEST)
    assert "范围：全部" in everything
    assert "上一场聊到挖矿" in everything
    assert "本场也聊挖矿" in everything


async def test_recall_does_not_return_dropped_speech(store: SQLiteDatabase) -> None:
    pk = await _session(store, started=1_000)
    await _streamer_says(store, pk, "这句没送到观众面前，提到挖矿", 1_100, dropped_at_ms=1_150)
    service = await _service(store, current_session=pk)

    text = await service.recall("挖矿")
    assert "这句没送到" not in text
    assert "没找到相关的记忆" in text


async def test_recall_searches_archive_lines_of_previous_session(store: SQLiteDatabase) -> None:
    first = await _session(store, started=1_000, ended=2_000)
    current = await _session(store, started=3_000)
    chat_id = await _streamer_says(store, first, "今天挖到钻石了", 1_100)
    await store.archive.insert_line(
        live_session_id=first,
        text="挖矿收获不错",
        quote="今天挖到钻石了",
        evidence_chat_id=chat_id,
    )
    service = await _service(store, current_session=current)

    text = await service.recall("挖矿")
    assert "上一场" in text
    assert "（档案「挖矿收获不错」）" in text
    assert "「今天挖到钻石了」" in text


async def test_recall_gives_a_snapshot_row_once_dated_by_where_it_was_said(store: SQLiteDatabase) -> None:
    """第二场的快照接住了第一场的一行：快照只引用原行，回想只给一条，时间按写下它的那一场标（之前）。"""
    first = await _session(store, started=1_000, ended=2_000)
    second = await _session(store, started=3_000, ended=4_000)
    current = await _session(store, started=5_000)
    chat_id = await _streamer_says(store, first, "从今天起你就叫团子", 1_100)
    await _streamer_says(store, second, "今天接着种菜", 3_100)
    line_id = await store.archive.insert_line(
        live_session_id=first, text="女仆取名团子", quote="从今天起你就叫团子", evidence_chat_id=chat_id
    )
    await store.sessions.apply_settlement(
        live_session_id=second,
        archive_lines=[],
        commitments=[],
        closures=[],
        settled_at_ms=4_500,
        now_ms_value=4_500,
        snapshot_kept=[KeptSnapshotLine(archive_line_id=line_id, lasting=True, seen_at_ms=1_000)],
    )
    service = await _service(store, current_session=current)

    text = await service.recall("团子")

    archive_hits = [line for line in _hit_lines(text) if "档案「女仆取名团子」" in line]
    assert len(archive_hits) == 1
    assert "上一场" not in archive_hits[0]


# ---------------------------------------------------------------------------
# 承诺
# ---------------------------------------------------------------------------


async def test_recall_reports_commitment_status_and_close_quote(store: SQLiteDatabase) -> None:
    pk = await _session(store, started=1_000)
    assert await store.commitments.insert_commitment(
        what="下次带大家打末影龙", quote="下次带你们打末影龙", created_session_id=pk, now_ms_value=1_500
    )
    closed_id = await store.commitments.insert_commitment(
        what="下次玩恐怖游戏", quote="下次玩恐怖游戏", now_ms_value=1_600
    )
    assert closed_id is not None
    await store.commitments.close_commitment(commitment_id=closed_id, close_quote="恐怖游戏玩过了，好吓人")
    service = await _service(store, current_session=pk)

    text = await service.recall("下次")
    assert "你答应过（未了结）下次带大家打末影龙" in text
    assert "原话「下次带你们打末影龙」" in text
    assert "你答应过（已了结）下次玩恐怖游戏" in text
    assert "了结时你说「恐怖游戏玩过了，好吓人」" in text


async def test_recall_skips_viewer_specific_and_void_commitments(store: SQLiteDatabase) -> None:
    pk = await _session(store, started=1_000)
    viewer_id = await seed_viewer(store.viewers, platform="bilibili", user_id="u1", user_name="老王", timestamp_ms=900)
    await store.commitments.insert_commitment(
        what="给老王盖房子", quote="下次给你盖房子", viewer_id=viewer_id, now_ms_value=1_500
    )
    void_id = await store.commitments.insert_commitment(what="明天讲鬼故事", quote="明天讲鬼故事", now_ms_value=1_600)
    assert void_id is not None
    await store.commitments.void_commitment(commitment_id=void_id)
    service = await _service(store, current_session=pk)

    no_viewer_commitment = await service.recall("盖房子")
    assert "给老王盖房子" not in no_viewer_commitment
    assert "没找到相关的记忆" in no_viewer_commitment

    no_void = await service.recall("鬼故事")
    assert "明天讲鬼故事" not in no_void
    assert "没找到相关的记忆" in no_void


# ---------------------------------------------------------------------------
# 遮名与昵称关键词
# ---------------------------------------------------------------------------


async def test_recall_masks_names_across_sessions_but_not_this_stream(store: SQLiteDatabase) -> None:
    await seed_viewer(store.viewers, platform="bilibili", user_id="u1", user_name="老王", timestamp_ms=900)
    first = await _session(store, started=1_000, ended=2_000)
    current = await _session(store, started=3_000)
    await _streamer_says(store, first, "老王你上次说的挖矿我记着呢", 1_100)
    await _streamer_says(store, current, "老王你来了，接着挖矿", 3_100)
    service = await _service(store, current_session=current)

    text = await service.recall("挖矿")
    assert "某位观众你上次说的挖矿我记着呢" in text  # 跨场结果遮名
    assert "老王你上次说的" not in text
    assert "老王你来了，接着挖矿" in text  # 本场结果不遮


async def test_recall_ignores_viewer_name_keywords_with_notice(store: SQLiteDatabase) -> None:
    await seed_viewer(store.viewers, platform="bilibili", user_id="u1", user_name="老王", timestamp_ms=900)
    pk = await _session(store, started=1_000)
    await _streamer_says(store, pk, "今天挖矿很顺利", 1_100)
    service = await _service(store, current_session=pk)

    text = await service.recall("老王 挖矿")
    assert "（回想工具不按观众名字查，已忽略 1 个名字）" in text
    assert "关键词「老王 挖矿」" in text  # 抬头照抄原查询词，便于核对
    assert "今天挖矿很顺利" in text

    only_name = await service.recall("老王")
    assert "已忽略 1 个名字" in only_name
    assert "没有可用的关键词了" in only_name


# ---------------------------------------------------------------------------
# 空结果与异常
# ---------------------------------------------------------------------------


async def test_recall_returns_plain_note_when_nothing_matches(store: SQLiteDatabase) -> None:
    pk = await _session(store, started=1_000)
    await _streamer_says(store, pk, "今天挖矿很顺利", 1_100)
    service = await _service(store, current_session=pk)

    text = await service.recall("养猫")
    assert "0 条" in text
    assert "没找到相关的记忆" in text


async def test_recall_this_stream_without_live_session_says_so(store: SQLiteDatabase) -> None:
    service = await _service(store, current_session=None)
    text = await service.recall("挖矿", SCOPE_THIS_STREAM)
    assert "0 条" in text
    assert "现在不在直播" in text


async def test_recall_empty_query_asks_for_keywords(store: SQLiteDatabase) -> None:
    service = await _service(store, current_session=None)
    assert "想查什么" in await service.recall("  ")


class _ExplodingChatRepo:
    """读面一调用就抛错的仓储替身：验证查询异常转成平实说明、不反噬决策轮。

    只用作故障注入（不是可复用的正常替身），所以留在本测试文件内。
    """

    async def list_chat_session_bounds(self) -> List[Any]:
        raise RuntimeError("数据库炸了")


async def test_recall_query_failure_returns_note_without_raising() -> None:
    service = MemoryRecallService(
        chat_repo=_ExplodingChatRepo(),
        commitments_repo=None,
        archive_repo=None,
        viewer_repo=None,
        current_session_provider=lambda: None,
    )
    assert "没查到" in await service.recall("挖矿")


# ---------------------------------------------------------------------------
# Provider 组装
# ---------------------------------------------------------------------------


async def test_provider_exposes_memory_recall_tool(store: SQLiteDatabase) -> None:
    pk = await _session(store, started=1_000)
    await _streamer_says(store, pk, "今天挖矿很顺利", 1_100)
    provider = build_memory_recall_provider(await _service(store, current_session=pk))

    assert provider.name == MEMORY_RECALL_PROVIDER_NAME
    assert {spec.full_name for spec in provider.list_tools()} == {MEMORY_RECALL_TOOL_NAME}

    result = await provider.invoke(ToolInvocation(tool_name=MEMORY_RECALL_TOOL_NAME, arguments={"query": "挖矿"}))
    assert result.success, result.error_message
    assert "今天挖矿很顺利" in result.content
    assert "范围：全部" in result.content  # 缺省 scope = all
    assert "顺序：最新在前" in result.content  # 缺省 order = newest

    # 空 query 走业务说明（不是执行失败）
    empty = await provider.invoke(ToolInvocation(tool_name=MEMORY_RECALL_TOOL_NAME, arguments={}))
    assert empty.success
    assert "想查什么" in empty.content


async def test_recall_marks_which_save_game_memories_belong_to(store: SQLiteDatabase) -> None:
    """眼下在新档：老档里的档案行、承诺注明不是眼下这个存档；那场只在老档玩过时，她那场说过的话也注明。"""
    old = await _session(store, started=1_000, ended=2_000)
    await _streamer_says(store, old, "屋顶终于封好了", 1_100)
    await store.events.insert_game_event(
        live_session_id=old,
        game="minecraft",
        event_type="milestone",
        message="m",
        timestamp_ms=1_050,
        save_id="a",
        save_name="老档",
    )
    await store.archive.insert_line(
        live_session_id=old,
        text="Minecraft：屋顶封好了",
        quote="屋顶终于封好了",
        evidence_chat_id=1,
        game="minecraft",
        save_id="a",
        save_name="老档",
    )
    await store.commitments.insert_commitment(what="屋顶再加一层", quote="屋顶下次再加一层", game="minecraft")
    await _index(store)
    service = MemoryRecallService(
        chat_repo=store.chat,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
        current_session_provider=lambda: None,
        clock=FakeClock().now_ms,
        event_repo=store.events,
        saves_provider=lambda: {"minecraft": GameSave(id="b", name="新档")},
        search=MemorySearch(repo=store.memory_index, embedding=None),
    )

    lines = _hit_lines(await service.recall("屋顶"))

    assert any("你说：「屋顶终于封好了」（那场 minecraft 在「老档」里，不是眼下这个存档）" in line for line in lines)
    assert any(
        "档案「Minecraft：屋顶封好了」" in line and "在 minecraft「老档」里，不是眼下这个存档" in line for line in lines
    )
    assert any("屋顶再加一层" in line and "在 minecraft 里，不确定是哪个存档" in line for line in lines)
    # 不分存档时一句都不注
    assert "存档" not in await (await _service(store)).recall("屋顶")


# ---------------------------------------------------------------------------
# 游戏进展
# ---------------------------------------------------------------------------


async def _game_event(store: SQLiteDatabase, pk: int, event_type: str, message: str, ts: int, save_id: str = "") -> int:
    return await store.events.insert_game_event(
        live_session_id=pk,
        game="minecraft",
        event_type=event_type,
        message=message,
        timestamp_ms=ts,
        save_id=save_id,
        save_name="老档" if save_id else "",
    )


async def test_recall_finds_game_milestones_and_reports_but_not_chat_or_alerts(store: SQLiteDatabase) -> None:
    """里程碑与阶段汇报是"做到哪一步"的事实，查得到；游戏聊天、安全阀不进索引，查不到。"""
    old = await _session(store, started=1_000, ended=2_000)
    await _streamer_says(store, old, "今天先到这里", 1_900)  # 有台词的场次才认得出"上一场"
    await _game_event(store, old, "milestone", "灯塔盖好了", 1_100)
    await _game_event(store, old, "report", "灯塔地基打完，还差塔身", 1_200)
    await _game_event(store, old, "chat", "玩家 某人：灯塔好丑", 1_300)
    await _game_event(store, old, "attention_required", "灯塔附近卡住了", 1_400)
    current = await _session(store, started=3_000)
    service = await _service(store, current_session=current)

    lines = _hit_lines(await service.recall("灯塔"))

    assert "- 上一场 游戏里（里程碑）：灯塔盖好了" in lines
    assert "- 上一场 游戏里（阶段汇报）：灯塔地基打完，还差塔身" in lines
    assert len(lines) == 2


async def test_recall_marks_the_save_of_game_progress_and_respects_this_stream(store: SQLiteDatabase) -> None:
    old = await _session(store, started=1_000, ended=2_000)
    await _streamer_says(store, old, "今天先到这里", 1_900)
    await _game_event(store, old, "milestone", "灯塔盖好了", 1_100, save_id="a")
    current = await _session(store, started=3_000)
    await _game_event(store, current, "report", "开始在新档盖灯塔", 3_100, save_id="b")
    await _index(store)
    service = MemoryRecallService(
        chat_repo=store.chat,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
        current_session_provider=lambda: current,
        clock=FakeClock().now_ms,
        event_repo=store.events,
        saves_provider=lambda: {"minecraft": GameSave(id="b", name="新档")},
        search=MemorySearch(repo=store.memory_index, embedding=None),
    )

    everything = _hit_lines(await service.recall("灯塔"))
    assert "- 上一场 游戏里（里程碑）：灯塔盖好了（在 minecraft「老档」里，不是眼下这个存档）" in everything
    this_stream = _hit_lines(await service.recall("灯塔", scope=SCOPE_THIS_STREAM))
    assert len(this_stream) == 1 and "开始在新档盖灯塔" in this_stream[0] and "就是眼下这个存档" in this_stream[0]
