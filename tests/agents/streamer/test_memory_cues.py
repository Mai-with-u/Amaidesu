"""线索推送：承诺线索、老观众在场一行与遗忘回执。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest

from src.agents.streamer.memory.cues import (
    MAX_CARDS_PER_BATCH,
    MAX_PUSHES_PER_CARD,
    PUSH_COOLDOWN_MS,
    MemoryCueProvider,
    is_social_fact_safe_for_game_lead,
)
from src.agents.streamer.memory.index import MemoryIndexer
from src.agents.streamer.memory.search import MemorySearch
from src.modules.events.payloads.room import RoomMessageUser
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import PendingArchiveLine, PendingViewerCandidate
from src.modules.storage.privacy import PrivacyService
from src.modules.storage.repos.viewer_candidates import VIEWER_SELF, replace_session_candidates
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation, NearbyPlace
from tests.modules.storage.helpers import make_room_message

_NOW = 1_000_000_000_000


class _Clock:
    """可拨动的时钟：线索推送的限频与档位词都按它算。"""

    def __init__(self, value: int = _NOW) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


@pytest.fixture()
async def store(tmp_path: Path):
    db = SQLiteDatabase(tmp_path / "cues.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture()
async def privacy(store: SQLiteDatabase, tmp_path: Path) -> PrivacyService:
    service = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "forget.key", bot_name="麦麦")
    await service.initialize()
    yield service
    await service.close()


async def _session(store: SQLiteDatabase, *, started_at_ms: int = _NOW) -> int:
    session_id = await store.sessions.insert_live_session(started_at_ms=started_at_ms)
    return session_id


async def _viewer(
    store: SQLiteDatabase,
    *,
    external_id: str,
    name: str,
    sessions_count: int = 0,
    prev_seen_ms: Optional[int] = None,
    forget_requested_at_ms: Optional[int] = None,
) -> int:
    result = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id=external_id,
        user_name=name,
        timestamp_ms=_NOW,
        name_match_window_ms=0,
    )
    viewer_id = result.viewer_id
    if sessions_count or prev_seen_ms is not None or forget_requested_at_ms is not None:
        await store.execute(
            "UPDATE viewers SET sessions_count=?, prev_seen_ms=?, forget_requested_at_ms=? WHERE id=?",
            (sessions_count, prev_seen_ms, forget_requested_at_ms, viewer_id),
        )
    return viewer_id


def _message(
    *,
    external_id: str = "u1",
    name: str = "阿杰",
    content: str = "主播好",
    message_id: str = "m1",
    simulated: bool = False,
) -> Any:
    return make_room_message(
        user=RoomMessageUser(id=external_id, name=name),
        content=content,
        message_id=message_id,
        simulated=simulated,
    )


def _provider(
    store: SQLiteDatabase,
    *,
    session_id: Optional[int],
    privacy: Optional[PrivacyService] = None,
    clock: Optional[_Clock] = None,
    **extra: Any,
) -> MemoryCueProvider:
    return MemoryCueProvider(
        commitments_repo=store.commitments,
        chat_repo=store.chat,
        viewer_repo=store.viewers,
        privacy_service=privacy,
        current_session_provider=lambda: session_id,
        clock=clock or _Clock(),
        **extra,
    )


# ---------------------------------------------------------------------------
# 承诺线索
# ---------------------------------------------------------------------------


async def test_viewer_commitment_card_pushes_when_the_viewer_speaks(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    viewer_id = await _viewer(store, external_id="u1", name="阿杰")
    commitment_id = await store.commitments.insert_commitment(
        what="在 Minecraft 里给 ta 盖个小房子", quote="阿杰别难过，下次给你盖个小房子", viewer_id=viewer_id
    )
    cues = await _provider(store, session_id=session_id).collect(batch=[_message()])

    assert commitment_id is not None
    assert cues.card_count == 1 and cues.commitment_ids == (commitment_id,)
    assert "[约#" in cues.text and "对阿杰" in cues.text and "盖个小房子" in cues.text
    # 对某位观众的卡不给原话：原话可能夹着 ta 的私事
    assert "别难过" not in cues.text


async def test_card_is_rate_limited_and_never_exceeds_two_pushes(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    viewer_id = await _viewer(store, external_id="u1", name="阿杰")
    await store.commitments.insert_commitment(what="给 ta 盖个小房子", viewer_id=viewer_id)
    clock = _Clock()
    provider = _provider(store, session_id=session_id, clock=clock)

    assert (await provider.collect(batch=[_message()])).card_count == 1
    # 十分钟内同一张卡不重复推
    clock.value += PUSH_COOLDOWN_MS // 2
    assert (await provider.collect(batch=[_message(message_id="m2")])).card_count == 0
    clock.value += PUSH_COOLDOWN_MS
    assert (await provider.collect(batch=[_message(message_id="m3")])).card_count == 1
    # 推满两次后本场不再推
    clock.value += PUSH_COOLDOWN_MS
    assert (await provider.collect(batch=[_message(message_id="m4")])).card_count == 0
    assert MAX_PUSHES_PER_CARD == 2


async def test_card_stops_once_the_line_referenced_it(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    viewer_id = await _viewer(store, external_id="u1", name="阿杰")
    commitment_id = await store.commitments.insert_commitment(what="给 ta 盖个小房子", viewer_id=viewer_id)
    assert commitment_id is not None
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=_NOW,
        sender_role="assistant",
        content="安心啦，回头就给你盖",
        message_type="speech",
        memory_refs=f'["约#{commitment_id}"]',
    )

    cues = await _provider(store, session_id=session_id).collect(batch=[_message()])
    assert cues.card_count == 0 and cues.text == ""


async def test_dropped_line_does_not_count_as_used(store: SQLiteDatabase) -> None:
    """没送到观众面前的台词不算"用上了"：引用它的那句没被听见，卡照推。"""
    session_id = await _session(store)
    viewer_id = await _viewer(store, external_id="u1", name="阿杰")
    commitment_id = await store.commitments.insert_commitment(what="给 ta 盖个小房子", viewer_id=viewer_id)
    assert commitment_id is not None
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=_NOW,
        sender_role="assistant",
        content="安心啦，回头就给你盖",
        message_type="speech",
        memory_refs=f'["约#{commitment_id}"]',
        dropped_at_ms=_NOW,
    )

    cues = await _provider(store, session_id=session_id).collect(batch=[_message()])
    assert cues.card_count == 1 and cues.commitment_ids == (commitment_id,)


async def test_new_session_resets_push_counters(store: SQLiteDatabase) -> None:
    first = await _session(store, started_at_ms=_NOW - 10_000)
    viewer_id = await _viewer(store, external_id="u1", name="阿杰")
    await store.commitments.insert_commitment(what="给 ta 盖个小房子", viewer_id=viewer_id)
    clock = _Clock()
    first_provider = _provider(store, session_id=first, clock=clock)
    assert (await first_provider.collect(batch=[_message()])).card_count == 1
    assert (await first_provider.collect(batch=[_message(message_id="m2")])).card_count == 0

    second = await _session(store, started_at_ms=_NOW)
    cues = await _provider(store, session_id=second, clock=clock).collect(batch=[_message(message_id="m3")])
    assert cues.card_count == 1


async def test_game_commitment_does_not_carousel_merely_because_game_is_open(store: SQLiteDatabase) -> None:
    """游戏承诺取消轮播：仅开着游戏不再每批轮推挂该游戏的承诺线索。"""
    session_id = await _session(store)
    await store.commitments.insert_commitment(what="继续炸 TNT", quote="下次接着炸 TNT", game="Minecraft")
    provider = _provider(store, session_id=session_id)

    closed = await provider.collect(batch=[_message()])
    assert closed.card_count == 0
    opened = await provider.collect(batch=[_message()], open_games=["Minecraft"])
    assert opened.card_count == 0 and opened.text == ""


async def test_cards_are_capped_per_batch(store: SQLiteDatabase) -> None:
    assert MAX_CARDS_PER_BATCH == 2
    session_id = await _session(store)
    for index in range(1, 6):
        viewer_id = await _viewer(store, external_id=f"u{index}", name=f"观众{index}")
        await store.commitments.insert_commitment(what=f"第 {index} 件事", viewer_id=viewer_id)
    batch = [_message(external_id=f"u{index}", name=f"观众{index}", message_id=f"m{index}") for index in range(1, 6)]

    cues = await _provider(store, session_id=session_id).collect(batch=batch)
    assert cues.card_count == MAX_CARDS_PER_BATCH
    assert len(cues.commitment_ids) == MAX_CARDS_PER_BATCH


# ---------------------------------------------------------------------------
# 老观众在场一行
# ---------------------------------------------------------------------------


async def test_presence_line_for_returning_viewers_until_she_replies(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    await _viewer(store, external_id="u1", name="小林", sessions_count=4, prev_seen_ms=_NOW - 60_000)
    cues = await _provider(store, session_id=session_id).collect(batch=[_message(external_id="u1", name="小林")])
    assert "老观众在场" in cues.text and "小林（前几天刚来过）" in cues.text

    # 她本场回应过 ta 之后这一行就不再出现
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=_NOW,
        sender_role="viewer",
        content="我回来啦",
        message_type="danmaku",
        message_id="m9",
        viewer_id=(await store.viewers.find_viewer_id(platform="bilibili", external_id="u1")),
    )
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=_NOW,
        sender_role="assistant",
        content="欢迎回来",
        message_type="speech",
        reply_to_message_id="m9",
    )
    after = await _provider(store, session_id=session_id).collect(batch=[_message(external_id="u1", name="小林")])
    assert "老观众在场" not in after.text


async def _returning_viewer(store: SQLiteDatabase) -> tuple[MemoryCueProvider, int]:
    """造一位老观众与 ta 本场那句弹幕，返回 (provider, 场次号)；回应由用例按需追加。"""
    session_id = await _session(store)
    viewer_id = await _viewer(store, external_id="u1", name="小林", sessions_count=4, prev_seen_ms=_NOW - 60_000)
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=_NOW,
        sender_role="viewer",
        content="我回来啦",
        message_type="danmaku",
        message_id="m9",
        viewer_id=viewer_id,
    )
    return _provider(store, session_id=session_id), session_id


async def _her_reply_to(store: SQLiteDatabase, session_id: int, *, dropped_at_ms: Optional[int]) -> None:
    """她回应 ta 的那句台词；dropped_at_ms 非空表示播放队列确认没送到观众面前。"""
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=_NOW,
        sender_role="assistant",
        content="欢迎回来",
        message_type="speech",
        reply_to_message_id="m9",
        dropped_at_ms=dropped_at_ms,
    )


async def test_dropped_reply_does_not_count_as_replied(store: SQLiteDatabase) -> None:
    """回应了但那一句没送到观众面前，不算回应过：老观众一行照出。"""
    provider, session_id = await _returning_viewer(store)
    await _her_reply_to(store, session_id, dropped_at_ms=_NOW)

    cues = await provider.collect(batch=[_message(external_id="u1", name="小林")])
    assert "老观众在场" in cues.text


async def test_replied_state_is_requeried_every_batch(store: SQLiteDatabase) -> None:
    """老观众一行的"已回应"判断每批重查、不整场缓存：同一实例里刚送达的回应，下一批立刻生效。"""
    provider, session_id = await _returning_viewer(store)
    before = await provider.collect(batch=[_message(external_id="u1", name="小林")])
    assert "老观众在场" in before.text, "还没回应：照常提示"

    await _her_reply_to(store, session_id, dropped_at_ms=None)
    after = await provider.collect(batch=[_message(external_id="u1", name="小林", message_id="m2")])
    assert "老观众在场" not in after.text, "回应已送达：同一实例的下一批就查到了，不用等换场"


async def test_presence_line_is_suppressed_when_a_card_is_pushed(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    viewer_id = await _viewer(store, external_id="u1", name="小林", sessions_count=5, prev_seen_ms=_NOW - 60_000)
    await store.commitments.insert_commitment(what="答应过的一件事", viewer_id=viewer_id)

    cues = await _provider(store, session_id=session_id).collect(batch=[_message(external_id="u1", name="小林")])
    assert cues.card_count == 1
    assert "老观众在场" not in cues.text


async def test_new_viewer_and_pending_deletion_viewers_are_not_presence(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    await _viewer(store, external_id="u1", name="新人", sessions_count=1, prev_seen_ms=_NOW - 60_000)
    await _viewer(
        store,
        external_id="u2",
        name="待删观众",
        sessions_count=6,
        prev_seen_ms=_NOW - 60_000,
        forget_requested_at_ms=_NOW - 1_000,
    )
    cues = await _provider(store, session_id=session_id).collect(
        batch=[_message(external_id="u1", name="新人"), _message(external_id="u2", name="待删观众")]
    )
    assert "老观众在场" not in cues.text


# ---------------------------------------------------------------------------
# 回执行与边界
# ---------------------------------------------------------------------------


async def test_forget_command_gets_a_notice_and_enables_retraction(
    store: SQLiteDatabase, privacy: PrivacyService
) -> None:
    """遗忘命令回执：忘了我即时回执；撤回短语只认记账侧确认已生效的那一次撤回。

    线索侧自己不猜——没经过记账入口的撤回短语不回执，否则会答应一场并不会发生的撤回。
    """
    session_id = await _session(store)
    await _viewer(store, external_id="u1", name="阿杰")
    provider = _provider(store, session_id=session_id, privacy=privacy)

    forget = await provider.collect(batch=[_message(content="忘了我吧", message_id="m815")])
    assert "【已按" in forget.text and "m815" in forget.text

    unhandled = await provider.collect(batch=[_message(content="开玩笑的", message_id="m821")])
    assert unhandled.text == "", "记账侧没处理过的撤回短语不回执"

    assert (
        await privacy.process_incoming_viewer_message(
            platform="bilibili",
            id_kind="account",
            external_id="u1",
            user_name="阿杰",
            message_type="danmaku",
            content="忘了我吧",
            message_id="m815",
        )
        == "forget_command"
    )
    assert (
        await privacy.process_incoming_viewer_message(
            platform="bilibili",
            id_kind="account",
            external_id="u1",
            user_name="阿杰",
            message_type="danmaku",
            content="开玩笑的",
            message_id="m821",
        )
        == "retracted"
    )

    retract = await provider.collect(batch=[_message(content="开玩笑的", message_id="m821")])
    assert "【已撤回" in retract.text and "m821" in retract.text


async def test_retraction_without_basis_gets_no_notice(store: SQLiteDatabase, privacy: PrivacyService) -> None:
    session_id = await _session(store)
    cues = await _provider(store, session_id=session_id, privacy=privacy).collect(
        batch=[_message(content="算了", message_id="m900")]
    )
    assert cues.text == ""


async def test_simulated_messages_never_trigger_cues(store: SQLiteDatabase, privacy: PrivacyService) -> None:
    session_id = await _session(store)
    viewer_id = await _viewer(store, external_id="u1", name="阿杰")
    await store.commitments.insert_commitment(what="给 ta 盖个小房子", viewer_id=viewer_id)

    cues = await _provider(store, session_id=session_id, privacy=privacy).collect(
        batch=[_message(content="忘了我吧", simulated=True)]
    )
    assert cues.text == ""


async def test_without_session_there_are_no_cues(store: SQLiteDatabase) -> None:
    viewer_id = await _viewer(store, external_id="u1", name="阿杰")
    await store.commitments.insert_commitment(what="给 ta 盖个小房子", viewer_id=viewer_id)
    cues = await _provider(store, session_id=None).collect(batch=[_message()])
    assert cues.text == "" and cues.card_count == 0


async def test_game_commitment_pushes_on_place_arrival_only_for_current_save_and_active_promise(
    store: SQLiteDatabase,
) -> None:
    """游戏承诺在走到提及的地点时单推：别的存档与已搁置的不推，眼下存档的活跃承诺单推。"""
    s1 = await store.sessions.insert_live_session(started_at_ms=_NOW - 90_000_000)
    await store.sessions.close_live_session(live_session_id=s1, ended_at_ms=_NOW - 86_000_000)
    s2 = await store.sessions.insert_live_session(started_at_ms=_NOW - 50_000_000)
    await store.sessions.close_live_session(live_session_id=s2, ended_at_ms=_NOW - 46_000_000)
    session_id = await _session(store)

    await store.commitments.insert_commitment(
        what="修北边木桥", quote="桥回头修完", game="minecraft", save_id="a", created_session_id=s2
    )
    # s1 说的带"明天"的短期承诺隔过 s2 没再提，已搁了很久
    await store.commitments.insert_commitment(
        what="明天去北边木桥挂灯", quote="明天去北边木桥挂灯", game="minecraft", save_id="b", created_session_id=s1
    )
    here = await store.commitments.insert_commitment(
        what="把北边木桥护栏补齐", quote="把北边木桥护栏补齐", game="minecraft", save_id="b", created_session_id=s2
    )
    provider = _passive(store, session_id, _Clock())
    current = {"minecraft": GameSave(id="b", name="新档")}

    # 先在 60 格外记下初始距离，再走进 24 格内触发到达
    far = {"minecraft": GameSituation(age_ms=1_000, places=[NearbyPlace(name="北边木桥", distance=60)])}
    near = {"minecraft": GameSituation(age_ms=1_000, places=[NearbyPlace(name="北边木桥", distance=12)])}
    assert (await provider.collect(batch=[], current_saves=current, situations=far)).card_count == 0
    cues = await provider.collect(batch=[], current_saves=current, situations=near)
    assert cues.commitment_ids == (here,)
    assert "把北边木桥护栏补齐" in cues.text
    assert "桥回头修完" not in cues.text and "明天去北边木桥挂灯" not in cues.text


async def test_viewer_card_says_which_save_the_promise_was_made_in(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    viewer_id = await _viewer(store, external_id="u1", name="阿杰")
    await store.commitments.insert_commitment(
        what="给 ta 盖个小房子", viewer_id=viewer_id, game="minecraft", save_id="a", save_name="老档"
    )
    cues = await _provider(store, session_id=session_id).collect(
        batch=[_message()], current_saves={"minecraft": GameSave(id="b", name="新档")}
    )

    assert cues.card_count == 1
    assert "在 minecraft「老档」里，不是眼下这个存档" in cues.text


# ---------------------------------------------------------------------------
# 被动想起：规则与地点两路的往事线索、老梗线索
# ---------------------------------------------------------------------------


async def _old_line(store: SQLiteDatabase, text: str = "Minecraft：女仆取名团子", **extra: Any) -> int:
    """一行之前正式场里的档案（默认带触发词"团子"）。"""
    old_session = await store.sessions.insert_live_session(started_at_ms=_NOW - 86_400_000)
    await store.sessions.close_live_session(live_session_id=old_session, ended_at_ms=_NOW - 80_000_000)
    extra.setdefault("keywords", ["团子"])
    line_id = await store.archive.insert_line(
        live_session_id=old_session, text=text, quote="就叫团子吧", evidence_chat_id=1, **extra
    )
    assert line_id
    return line_id


async def _her_line(store: SQLiteDatabase, session_id: int, content: str, *, refs: str = "", at: int = _NOW) -> None:
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=at,
        sender_role="assistant",
        content=content,
        message_type="speak",
        utterance_id=f"utt_{at}_{content}",
        memory_refs=refs or None,
    )


def _passive(store: SQLiteDatabase, session_id: int, clock: _Clock, **extra: Any) -> MemoryCueProvider:
    return _provider(
        store,
        session_id=session_id,
        clock=clock,
        archive_repo=store.archive,
        cue_log_repo=store.memory_cue_log,
        **extra,
    )


async def test_danmaku_keyword_pushes_archive_card_and_logs_it(store: SQLiteDatabase) -> None:
    """弹幕提到触发词：推那行档案（带 档#N，写为什么想起来），编号进点名名单，记一行记忆线索记录。"""
    session_id = await _session(store)
    line_id = await _old_line(store)
    clock = _Clock()

    cues = await _passive(store, session_id, clock).collect(batch=[_message(content="团子今天乖不乖", message_id="m7")])

    assert f"[档#{line_id}·之前] Minecraft：女仆取名团子（你说过「就叫团子吧」）" in cues.text
    assert "想起它是因为弹幕 m7 提到「团子」" in cues.text
    assert cues.archive_ids == (line_id,) and cues.commitment_ids == ()
    # 观众弹幕推上来的：算有人提到，本场提过的旧事这一窗再点名不退回
    assert cues.asked_archive_ids == (line_id,)
    (entry,) = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert (entry.card_kind, entry.archive_line_id, entry.sources) == ("rule", line_id, ["danmaku"])
    # 记录只存第几个触发词与匹配方式，不存原词
    assert entry.detail == {"keyword_index": [0], "how": ["exact"]} and entry.self_triggered is False
    assert "阿杰" not in str(entry.model_dump()) and "m7" not in str(entry.model_dump()), "记录不带观众身份"


async def test_archive_card_stops_once_she_used_it_and_backfills_the_log(store: SQLiteDatabase) -> None:
    """往事线索被她点名用上（档#N 进了台词的记忆引用），本场不再推，记录回填用上的时刻。"""
    session_id = await _session(store)
    line_id = await _old_line(store)
    clock = _Clock()
    provider = _passive(store, session_id, clock)
    assert (await provider.collect(batch=[_message(content="团子呢")])).archive_ids == (line_id,)

    await _her_line(store, session_id, "团子在家看门呢", refs=f'["档#{line_id}"]', at=_NOW + 1_000)
    clock.value += PUSH_COOLDOWN_MS + 1
    cues = await provider.collect(batch=[_message(content="团子呢", message_id="m2")])

    assert cues.archive_ids == () and "团子" not in cues.text
    (entry,) = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert entry.used_at_ms == clock.value


async def test_self_triggered_card_pushes_once_per_session(store: SQLiteDatabase) -> None:
    """自触发规则一路线索只在该行不在记忆摘要且来自两场以前才推（每场只推一次）；近两场内自触发不推，但观众弹幕提到照推。"""
    # 铺三场已完结正式场：s_old（三场前 = 两场以前）、s_prev2（上上场）、s_prev1（上一场）
    s_old = await store.sessions.insert_live_session(started_at_ms=_NOW - 300_000_000)
    await store.sessions.close_live_session(live_session_id=s_old, ended_at_ms=_NOW - 290_000_000)
    s_prev2 = await store.sessions.insert_live_session(started_at_ms=_NOW - 200_000_000)
    await store.sessions.close_live_session(live_session_id=s_prev2, ended_at_ms=_NOW - 190_000_000)
    s_prev1 = await store.sessions.insert_live_session(started_at_ms=_NOW - 100_000_000)
    await store.sessions.close_live_session(live_session_id=s_prev1, ended_at_ms=_NOW - 90_000_000)

    line_id = await store.archive.insert_line(
        live_session_id=s_old,
        text="Minecraft：女仆取名团子",
        quote="就叫团子吧",
        evidence_chat_id=1,
        keywords=["团子"],
    )
    recent_line_id = await store.archive.insert_line(
        live_session_id=s_prev2,
        text="Minecraft：新风车试转",
        quote="风车转了",
        evidence_chat_id=2,
        keywords=["风车"],
    )

    session_id = await _session(store)
    clock = _Clock()
    provider = _passive(store, session_id, clock)
    await _her_line(store, session_id, "说起来团子和风车最近怎么样了")

    # 若已在简报里，连两场以前的也不推
    assert (await provider.collect(batch=[], brief_archive_ids=[line_id])).archive_ids == ()

    # 不在简报里：两场以前的 line_id 推一次；上上场（近两场内）的 recent_line_id 自触发不推
    first = await provider.collect(batch=[])
    assert first.archive_ids == (line_id,) and "想起它是因为你刚说到「团子」" in first.text
    assert recent_line_id not in first.archive_ids
    assert first.asked_archive_ids == (), "只由她自己的台词想起的不算有人提到"
    clock.value += PUSH_COOLDOWN_MS + 1
    assert (await provider.collect(batch=[])).archive_ids == (), "自触发一场只推一次"

    clock.value += PUSH_COOLDOWN_MS + 1
    danmaku_round = await provider.collect(batch=[_message(content="团子和风车！")])
    assert set(danmaku_round.archive_ids) == {line_id, recent_line_id}, "观众提到时不限近两场照推"
    entries = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert [(entry.archive_line_id, entry.self_triggered, entry.sources) for entry in entries] == [
        (line_id, True, ["self"]),
        (recent_line_id, False, ["danmaku", "self"]),
        (line_id, False, ["danmaku", "self"]),
    ]


async def test_passive_skips_brief_lines_other_saves_and_quiet_viewers(
    store: SQLiteDatabase, privacy: PrivacyService
) -> None:
    """简报里列着的不推；别的存档里的事不推；发过"忘了我"的观众与模拟消息不触发；开关关掉整路不跑。"""
    session_id = await _session(store)
    listed = await _old_line(store, "Minecraft：女仆取名团子")
    other = await _old_line(store, "Minecraft：老档的小麦田", keywords=["小麦田"], game="minecraft", save_id="a")
    unsure = await _old_line(store, "Minecraft：挖到钻石", keywords=["钻石"], game="minecraft")
    await _viewer(store, external_id="u9", name="小林", forget_requested_at_ms=_NOW - 1)
    saves = {"minecraft": GameSave(id="b", name="新档")}
    provider = _passive(store, session_id, _Clock(), privacy=privacy)

    cues = await provider.collect(
        batch=[_message(content="团子 小麦田 钻石")], current_saves=saves, brief_archive_ids=[listed]
    )
    assert cues.archive_ids == (unsure,)
    assert "在 minecraft 里，不确定是哪个存档" in cues.text
    assert other not in cues.archive_ids

    quiet = await _passive(store, session_id, _Clock(), privacy=privacy).collect(
        batch=[
            _message(external_id="u9", name="小林", content="钻石呢"),
            _message(content="钻石呢", simulated=True),
        ],
        current_saves=saves,
    )
    assert quiet.archive_ids == ()

    off = await _passive(store, session_id, _Clock(), passive_enabled=False).collect(
        batch=[_message(content="钻石呢")], current_saves=saves
    )
    assert off.archive_ids == () and off.text == ""


async def test_walking_up_to_a_remembered_place_pushes_its_lines_once(store: SQLiteDatabase) -> None:
    """角色走进记住的地点 24 格内：推挂在那里、这个存档里的档案行；同一个地点一场只推一次。"""
    session_id = await _session(store)
    line_id = await _old_line(
        store, "Minecraft：北边农场种下小麦", keywords=[], game="minecraft", save_id="b", place="北边农场"
    )
    await _old_line(store, "Minecraft：老档农场", keywords=[], game="minecraft", save_id="a", place="北边农场")
    saves = {"minecraft": GameSave(id="b", name="新档")}
    clock = _Clock()
    provider = _passive(store, session_id, clock)

    def near(distance: int) -> dict[str, GameSituation]:
        return {"minecraft": GameSituation(places=(NearbyPlace(name="北边农场", distance=distance),))}

    assert (await provider.collect(batch=[], current_saves=saves, situations=near(80))).archive_ids == ()
    arrived = await provider.collect(batch=[], current_saves=saves, situations=near(12))
    assert arrived.archive_ids == (line_id,)
    assert "想起它是因为游戏里的位置显示你可能走到了「北边农场」附近" in arrived.text
    assert "记录上和眼下是同一个存档" in arrived.text and "就是眼下这个存档" not in arrived.text, "提示语气，不断言"

    clock.value += PUSH_COOLDOWN_MS + 1
    await provider.collect(batch=[], current_saves=saves, situations=near(90))
    assert (await provider.collect(batch=[], current_saves=saves, situations=near(5))).archive_ids == ()
    (entry,) = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert (entry.card_kind, entry.sources, entry.detail) == ("place", ["place"], {"distance": 12}), "记录不存地点名"


async def test_inside_joke_card_only_when_a_viewer_brings_it_up(store: SQLiteDatabase) -> None:
    """观众弹幕提到老梗栏里的梗或别名：推一张老梗线索；她自己说到不推（老梗栏写明别自己翻出来）。"""
    session_id = await _session(store)
    jokes = "倒立洗头/洗头挑战：她说过输了就倒立洗头"
    await _her_line(store, session_id, "今天不搞倒立洗头")
    provider = _passive(store, session_id, _Clock(), inside_jokes=jokes)

    assert (await provider.collect(batch=[])).text == ""
    cues = await provider.collect(batch=[_message(content="洗头挑战什么时候", message_id="m3")])
    assert "[老梗] 弹幕 m3 提到了「倒立洗头」" in cues.text
    (entry,) = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert entry.card_kind == "joke" and entry.detail == {"keyword_index": [1], "how": ["exact"], "joke_index": 0}


async def test_familiar_viewer_card_logs_without_nickname_and_familiarity_map_persists_after_reply(
    store: SQLiteDatabase, privacy: PrivacyService
) -> None:
    """老观众发言：有已批准近况或共同造梗时推老观众线索（cue_log 不落昵称、压制重复在场行）；本场回复后卡停推但熟悉度映射照常保留，发忘了我立即清空。"""
    old_session = await store.sessions.insert_live_session(started_at_ms=_NOW - 200_000_000, kind="live")
    await store.sessions.close_live_session(live_session_id=old_session, ended_at_ms=_NOW - 190_000_000)

    # 阿杰：上过舰的老熟人（close），有一条已批准近况 + 一条共同造梗档案行
    ajie_id = await _viewer(store, external_id="u_ajie", name="阿杰", sessions_count=5, prev_seen_ms=_NOW - 60_000)
    await store.execute(
        "INSERT INTO guards(live_session_id, timestamp_ms, platform, user_id, user_name, viewer_id, simulated)"
        " VALUES (?, ?, 'bilibili', 'u_ajie', '阿杰', ?, 0)",
        (old_session, _NOW - 195_000_000, ajie_id),
    )
    # 新人：首次来访（none）
    await _viewer(store, external_id="u_new", name="小夏", sessions_count=1, prev_seen_ms=None)

    said = await store.chat.insert_live_chat(
        live_session_id=old_session,
        timestamp_ms=_NOW - 195_000_000,
        sender_role="viewer",
        viewer_id=ajie_id,
        content="我平时爱拼木模型，小羊叫绒球吧",
        message_type="danmaku",
    )
    replied = await store.chat.insert_live_chat(
        live_session_id=old_session,
        timestamp_ms=_NOW - 194_900_000,
        sender_role="assistant",
        content="木模型很有趣，小羊就叫绒球",
        message_type="speech",
    )
    await store.sessions.apply_settlement(
        live_session_id=old_session,
        archive_lines=[PendingArchiveLine(text="搭好牧场围栏", quote="围栏", evidence_chat_id=replied + 50)],
        commitments=[],
        closures=[],
        settled_at_ms=_NOW - 190_000_000,
        now_ms_value=_NOW - 190_000_000,
    )
    with store.manager.transaction() as conn:
        replace_session_candidates(
            conn,
            live_session_id=old_session,
            candidates=[
                PendingViewerCandidate(
                    kind="viewer_defined",
                    title="小羊取名绒球",
                    viewer_chat_id=said,
                    viewer_quote="小羊叫绒球",
                    word="绒球",
                    reply_chat_id=replied,
                    reply_quote="小羊就叫绒球",
                    viewer_id=ajie_id,
                ),
                PendingViewerCandidate(
                    kind=VIEWER_SELF,
                    title="ta 平时爱拼木模型",
                    viewer_chat_id=said,
                    viewer_quote="爱拼木模型",
                    word="木模型",
                    reply_chat_id=replied,
                    reply_quote="木模型很有趣",
                    viewer_id=ajie_id,
                ),
            ],
            created_at_ms=_NOW - 190_000_000,
        )
    for item in await store.viewer_candidates.list_candidates(status="pending"):
        await store.viewer_candidates.approve(candidate_id=item.id, at_ms=_NOW - 189_000_000)

    session_id = await _session(store)
    clock = _Clock()
    provider = _passive(store, session_id, clock, privacy=privacy)

    first = await provider.collect(
        batch=[
            _message(external_id="u_ajie", name="阿杰", content="晚上好呀", message_id="m_ajie_1"),
            _message(external_id="u_ajie", name="阿杰", content="今天还去牧场吗", message_id="m_ajie_2"),
            _message(external_id="u_new", name="小夏", content="第一次来", message_id="m_new_1"),
        ]
    )
    assert "[老观众·阿杰]" in first.text
    assert "ta 跟你说过：ta 平时爱拼木模型。" in first.text
    assert "当初「小羊取名绒球」就是和 ta 一起定的。" in first.text
    assert "老观众在场" not in first.text, "已推老观众线索时不再叠重复的老观众在场行"
    assert dict(first.familiarity_by_message_id) == {"m_ajie_1": "close", "m_ajie_2": "close"}

    (entry,) = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert (entry.card_kind, entry.sources, entry.detail) == (
        "viewer_familiar",
        ["viewer_presence"],
        {"viewer_id": ajie_id},
    )
    assert "阿杰" not in str(entry.model_dump()), "cue_log 绝不落观众昵称"

    # 本场主播已回应过阿杰后：老观众线索停推，但 familiarity_by_message_id 依然把阿杰的新弹幕标为 close
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=clock.value,
        sender_role="viewer",
        viewer_id=ajie_id,
        content="晚上好呀",
        message_type="danmaku",
        message_id="m_ajie_1",
    )
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=clock.value + 100,
        sender_role="assistant",
        content="晚上好阿杰，等下就去牧场看看",
        message_type="speech",
        reply_to_message_id="m_ajie_1",
    )
    clock.value += PUSH_COOLDOWN_MS + 1_000
    after_reply = await provider.collect(
        batch=[_message(external_id="u_ajie", name="阿杰", content="绒球还在吗", message_id="m_ajie_3")]
    )
    assert "[老观众·阿杰]" not in after_reply.text
    assert dict(after_reply.familiarity_by_message_id) == {"m_ajie_3": "close"}

    # 一旦发送"忘了我"命令，当场清掉缓存，不再输出熟悉度映射也不推老观众线索
    forget_round = await provider.collect(
        batch=[_message(external_id="u_ajie", name="阿杰", content="忘了我吧", message_id="m_ajie_forget")]
    )
    assert dict(forget_round.familiarity_by_message_id) == {}
    assert "[老观众·阿杰]" not in forget_round.text


# ---------------------------------------------------------------------------
# 游戏主控态：按意图触发联想（跨场档案 + 现行游戏社交事实）
# ---------------------------------------------------------------------------


async def _insert_game_chat_evidence(
    store: SQLiteDatabase,
    *,
    live_session_id: int,
    game: str,
    save_id: str,
    content: str,
    save_name: str = "主档",
) -> int:
    """插入一条真实的游戏公屏玩家聊天行，返回主键供社交事实作防投毒证据。"""
    with store.manager.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO game_chat(live_session_id, game, direction, content, save_id, save_name, at_ms, message_id)"
            " VALUES (?, ?, 'player', ?, ?, ?, ?, ?)",
            (live_session_id, game, content, save_id, save_name, _NOW - 50_000, "gc_1"),
        )
        return int(cur.lastrowid or 0)


async def _insert_danmaku_evidence(
    store: SQLiteDatabase,
    *,
    live_session_id: int,
    content: str,
) -> int:
    """插入一条真实直播间观众弹幕行，返回主键用于验证防弹幕投毒拦截。"""
    return await store.chat.insert_live_chat(
        live_session_id=live_session_id,
        timestamp_ms=_NOW - 50_000,
        sender_role="viewer",
        content=content,
        message_type="danmaku",
        message_id="dm_ev_1",
    )


@pytest.mark.parametrize("with_fts_index", [False, True])
async def test_game_lead_intent_recalls_social_fact_and_archive_without_embedding(
    store: SQLiteDatabase, with_fts_index: bool
) -> None:
    """游戏主控态不配向量模型时，按意图文本精准浮出被前 6 条截断的 works 事实与跨场档案，并遵守限频与记账。"""
    old_session = await store.sessions.insert_live_session(started_at_ms=_NOW - 86_400_000)
    await store.sessions.close_live_session(live_session_id=old_session, ended_at_ms=_NOW - 80_000_000)
    current_session = await _session(store)

    game = "block_game"
    save_id = "save_alpha"
    saves = {game: GameSave(id=save_id, name="主世界")}

    # 先写入 6 条挂在常驻公共规矩里的事实，以及第 7 条被前 6 条上限截断的 works 工程现状事实
    resident_texts = [
        "这是多人共建生存服",
        "PlayerA 负责基地仓库分类",
        "PlayerB 负责北边牧场管理",
        "公共仓库的木头可以按需取用",
        "夜里外出探险记得带床",
        "矿道入口插火把标记方向",
    ]
    resident_excludes: list[str] = []
    for idx, text in enumerate(resident_texts):
        gc_id = await _insert_game_chat_evidence(
            store, live_session_id=old_session, game=game, save_id=save_id, content=text
        )
        kind = "sharing" if idx >= 3 else "setting"
        await store.social_facts.insert_fact(
            kind=kind,
            text=text,
            quote=text,
            game=game,
            save_id=save_id,
            save_name="主世界",
            evidence_game_chat_id=gc_id,
            created_session_id=old_session,
            now_ms_value=_NOW - 80_000_000 + idx,
        )
        prefix = "共享惯例：" if kind == "sharing" else "环境形态："
        resident_excludes.append(f"{prefix}{text}")

    # 第 7 条被前 6 条上限截断的 works 事实：游戏公屏有实证
    portal_gc_id = await _insert_game_chat_evidence(
        store,
        live_session_id=old_session,
        game=game,
        save_id=save_id,
        content="营地本来就有一座现成的下界门，别再重新砌啦",
    )
    portal_fact_id = await store.social_facts.insert_fact(
        kind="works",
        text="营地本来就有一座现成的下界门",
        quote="营地本来就有一座现成的下界门",
        game=game,
        save_id=save_id,
        save_name="主世界",
        evidence_game_chat_id=portal_gc_id,
        created_session_id=old_session,
        now_ms_value=_NOW - 79_000_000,
    )
    assert portal_fact_id is not None

    # 往期正式场跨场档案（不设 keywords 与 place，模拟纯文本档案靠意图关键词/FTS 命中）
    archive_id = await store.archive.insert_line(
        live_session_id=old_session,
        text="营地西侧点亮了下界门",
        quote="这座下界门终于点亮了",
        evidence_chat_id=1,
        game=game,
        save_id=save_id,
        save_name="主世界",
        keywords=[],
        place="",
    )
    assert archive_id > 0

    search: Optional[MemorySearch] = None
    if with_fts_index:
        indexer = MemoryIndexer(repo=store.memory_index, embedding=None, embedding_note="test-no-vec")
        await indexer.run_once()
        search = MemorySearch(repo=store.memory_index, embedding=None)

    clock = _Clock()
    provider = _passive(
        store,
        current_session,
        clock,
        social_fact_repo=store.social_facts,
        search=search,
    )

    cues = await provider.cues_for_game_lead(
        game=game,
        texts=("凑钻石镐去砌下界门",),
        exclude_texts=resident_excludes,
        current_saves=saves,
        limit=2,
    )
    assert len(cues) == 2
    assert cues[0] == "[存档记忆·记着的事] 营地本来就有一座现成的下界门"
    assert "事实#" not in cues[0], "社交事实卡面绝不暴露内部数据库主键"
    assert "营地西侧点亮了下界门" in cues[1]

    # memory_cue_log 正常写入两条记录且不含错误
    entries = await store.memory_cue_log.list_session_entries(live_session_id=current_session)
    assert len(entries) == 2
    assert {entry.card_kind for entry in entries} == {"social_fact", "intent"}
    fact_entry = next(entry for entry in entries if entry.card_kind == "social_fact")
    assert fact_entry.archive_line_id is None and fact_entry.commitment_id is None
    assert fact_entry.detail["social_fact_id"] == portal_fact_id

    # 10 分钟限频与本场去重：紧接着再次触发同计划不再重复推；过冷却可推第 2 次；推满 2 次后停推
    assert (
        await provider.cues_for_game_lead(
            game=game,
            texts=("准备砌一座下界门",),
            exclude_texts=resident_excludes,
            current_saves=saves,
            limit=2,
        )
        == []
    )
    clock.value += PUSH_COOLDOWN_MS + 1
    second = await provider.cues_for_game_lead(
        game=game,
        texts=("准备砌一座下界门",),
        exclude_texts=resident_excludes,
        current_saves=saves,
        limit=2,
    )
    assert len(second) == 2
    clock.value += PUSH_COOLDOWN_MS + 1
    assert (
        await provider.cues_for_game_lead(
            game=game,
            texts=("准备砌一座下界门",),
            exclude_texts=resident_excludes,
            current_saves=saves,
            limit=2,
        )
        == []
    )


async def test_game_lead_intent_skips_facts_already_in_exclude_texts(store: SQLiteDatabase) -> None:
    """已在 [当前存档公共规矩]（带「共享惯例：」前缀）里的事实绝不重复推送。"""
    old_session = await store.sessions.insert_live_session(started_at_ms=_NOW - 86_400_000)
    await store.sessions.close_live_session(live_session_id=old_session, ended_at_ms=_NOW - 80_000_000)
    current_session = await _session(store)

    game = "block_game"
    save_id = "save_alpha"
    saves = {game: GameSave(id=save_id, name="主世界")}
    gc_id = await _insert_game_chat_evidence(
        store,
        live_session_id=old_session,
        game=game,
        save_id=save_id,
        content="公共熔炉区里的铁锭随便拿",
    )
    await store.social_facts.insert_fact(
        kind="sharing",
        text="公共熔炉区里的铁锭随便拿",
        quote="公共熔炉区里的铁锭随便拿",
        game=game,
        save_id=save_id,
        save_name="主世界",
        evidence_game_chat_id=gc_id,
        created_session_id=old_session,
        now_ms_value=_NOW - 79_000_000,
    )

    provider = _passive(store, current_session, _Clock(), social_fact_repo=store.social_facts)
    excluded = await provider.cues_for_game_lead(
        game=game,
        texts=("去公共熔炉区拿铁锭",),
        exclude_texts=("共享惯例： 公共熔炉区里的铁锭随便拿（上一场）",),
        current_saves=saves,
        limit=2,
    )
    assert excluded == []

    # 取消该条排除后，同条事实被精准推送
    included = await provider.cues_for_game_lead(
        game=game,
        texts=("去公共熔炉区拿铁锭",),
        exclude_texts=(),
        current_saves=saves,
        limit=2,
    )
    assert included == ["[存档记忆·共享惯例] 公共熔炉区里的铁锭随便拿"]


@pytest.mark.parametrize(
    ("scenario", "expected_pushed"),
    [
        ("danmaku_unlocked", False),
        ("mixed_danmaku_unlocked", False),
        ("danmaku_human_locked", True),
        ("no_evidence_unlocked", False),
        ("other_save", False),
        ("other_game", False),
        ("open_question", False),
        ("her_role", False),
        ("superseded", False),
    ],
)
async def test_game_lead_intent_firewall_blocks_poisoned_other_save_and_invalid_facts(
    store: SQLiteDatabase,
    scenario: str,
    expected_pushed: bool,
) -> None:
    """安全防火墙：拦截未锁定弹幕来源、无证据来源、他档/他游戏、非行动类及已取代事实；放行人工锁定事实。"""
    old_session = await store.sessions.insert_live_session(started_at_ms=_NOW - 86_400_000)
    await store.sessions.close_live_session(live_session_id=old_session, ended_at_ms=_NOW - 80_000_000)
    current_session = await _session(store)

    game = "block_game"
    save_id = "save_alpha"
    saves = {game: GameSave(id=save_id, name="主世界")}

    gc_id = await _insert_game_chat_evidence(
        store,
        live_session_id=old_session,
        game=game,
        save_id=save_id,
        content="营地东边已经建好了自动刷石机",
    )
    dm_id = await _insert_danmaku_evidence(
        store,
        live_session_id=old_session,
        content="弹幕说营地东边有自动刷石机",
    )

    fact_kwargs: dict[str, Any] = {
        "kind": "works",
        "text": "营地东边已经建好了自动刷石机",
        "quote": "营地东边已经建好了自动刷石机",
        "game": game,
        "save_id": save_id,
        "save_name": "主世界",
        "evidence_game_chat_id": gc_id,
        "evidence_chat_id": None,
        "human_locked": 0,
        "created_session_id": old_session,
        "now_ms_value": _NOW - 79_000_000,
    }

    if scenario == "danmaku_unlocked":
        fact_kwargs["evidence_game_chat_id"] = None
        fact_kwargs["evidence_chat_id"] = dm_id
    elif scenario == "mixed_danmaku_unlocked":
        fact_kwargs["evidence_chat_id"] = dm_id
    elif scenario == "danmaku_human_locked":
        fact_kwargs["evidence_game_chat_id"] = None
        fact_kwargs["evidence_chat_id"] = dm_id
        fact_kwargs["human_locked"] = 1
    elif scenario == "no_evidence_unlocked":
        fact_kwargs["evidence_game_chat_id"] = None
        fact_kwargs["evidence_chat_id"] = None
    elif scenario == "other_save":
        fact_kwargs["save_id"] = "save_beta"
        fact_kwargs["save_name"] = "二周目"
    elif scenario == "other_game":
        fact_kwargs["game"] = "other_game"
    elif scenario == "open_question":
        fact_kwargs["kind"] = "open_question"
        fact_kwargs["human_locked"] = 1
    elif scenario == "her_role":
        fact_kwargs["kind"] = "her_role"
        fact_kwargs["human_locked"] = 1

    fact_id = await store.social_facts.insert_fact(**fact_kwargs)
    assert fact_id is not None

    if scenario == "superseded":
        new_id = await store.social_facts.insert_fact(
            kind="works",
            text="西边码头栈桥正在扩建",
            quote="西边码头栈桥正在扩建",
            game=game,
            save_id=save_id,
            save_name="主世界",
            evidence_game_chat_id=gc_id,
            supersedes_id=fact_id,
            created_session_id=old_session,
            now_ms_value=_NOW - 78_000_000,
        )
        assert new_id is not None

    loaded = await store.social_facts.get_fact(fact_id=fact_id)
    assert loaded is not None
    assert is_social_fact_safe_for_game_lead(loaded, game=game, current_saves=saves) is expected_pushed

    provider = _passive(store, current_session, _Clock(), social_fact_repo=store.social_facts)
    cues = await provider.cues_for_game_lead(
        game=game,
        texts=("修一下自动刷石机",),
        current_saves=saves,
        limit=2,
    )
    if expected_pushed:
        assert cues == ["[存档记忆·记着的事] 营地东边已经建好了自动刷石机"]
    else:
        assert cues == []


@pytest.mark.parametrize(
    "current_saves",
    [
        {},
        {"block_game": GameSave(id="", name="")},
    ],
)
async def test_game_lead_intent_unknown_save_blocks_other_save_and_allows_generic_fact(
    store: SQLiteDatabase,
    current_saves: dict[str, GameSave],
) -> None:
    """当前存档未知或未上报时：带明确 save_id 的他档事实一律拦截，仅放行 save_id='' 的整游戏通用事实。"""
    old_session = await store.sessions.insert_live_session(started_at_ms=_NOW - 86_400_000)
    await store.sessions.close_live_session(live_session_id=old_session, ended_at_ms=_NOW - 80_000_000)
    current_session = await _session(store)

    game = "block_game"
    other_gc = await _insert_game_chat_evidence(
        store,
        live_session_id=old_session,
        game=game,
        save_id="save_other",
        content="二周目北边已经建好了自动刷石机",
    )
    other_fact_id = await store.social_facts.insert_fact(
        kind="works",
        text="二周目北边已经建好了自动刷石机",
        quote="二周目北边已经建好了自动刷石机",
        game=game,
        save_id="save_other",
        save_name="二周目",
        evidence_game_chat_id=other_gc,
        created_session_id=old_session,
        now_ms_value=_NOW - 79_000_000,
    )
    generic_gc = await _insert_game_chat_evidence(
        store,
        live_session_id=old_session,
        game=game,
        save_id="",
        content="公用附魔台放在图书馆二楼",
    )
    generic_fact_id = await store.social_facts.insert_fact(
        kind="sharing",
        text="公用附魔台放在图书馆二楼",
        quote="公用附魔台放在图书馆二楼",
        game=game,
        save_id="",
        save_name="",
        evidence_game_chat_id=generic_gc,
        created_session_id=old_session,
        now_ms_value=_NOW - 78_000_000,
    )
    assert other_fact_id is not None and generic_fact_id is not None

    other_fact = await store.social_facts.get_fact(fact_id=other_fact_id)
    generic_fact = await store.social_facts.get_fact(fact_id=generic_fact_id)
    assert other_fact is not None and generic_fact is not None
    assert is_social_fact_safe_for_game_lead(other_fact, game=game, current_saves=current_saves) is False
    assert is_social_fact_safe_for_game_lead(generic_fact, game=game, current_saves=current_saves) is True

    provider = _passive(store, current_session, _Clock(), social_fact_repo=store.social_facts)
    assert (
        await provider.cues_for_game_lead(
            game=game,
            texts=("修一下自动刷石机",),
            current_saves=current_saves,
            limit=2,
        )
        == []
    )
    assert await provider.cues_for_game_lead(
        game=game,
        texts=("去图书馆找附魔台",),
        current_saves=current_saves,
        limit=2,
    ) == ["[存档记忆·共享惯例] 公用附魔台放在图书馆二楼"]


@pytest.mark.parametrize("ellipsis", ["…", "..."])
async def test_game_lead_intent_skips_truncated_long_social_rule_with_ellipsis(
    store: SQLiteDatabase, ellipsis: str
) -> None:
    """超过 75 字的常驻公共规矩被截断为 80 字带省略号后，exclude_texts 依然准确去重且短截断不误杀。"""
    old_session = await store.sessions.insert_live_session(started_at_ms=_NOW - 86_400_000)
    await store.sessions.close_live_session(live_session_id=old_session, ended_at_ms=_NOW - 80_000_000)
    current_session = await _session(store)

    game = "block_game"
    save_id = "save_alpha"
    saves = {game: GameSave(id=save_id, name="主世界")}
    long_text = (
        "西侧主仓库一楼的储物箱已经全部按矿物木材红石农作物工具分类贴好告示牌，"
        "大家拿取附魔书与铁锭后请尽量放回对应格子里，避免后续找物资翻半天找不到耽误全队建造进度"
    )
    assert len(long_text) >= 76

    gc_id = await _insert_game_chat_evidence(
        store, live_session_id=old_session, game=game, save_id=save_id, content=long_text
    )
    await store.social_facts.insert_fact(
        kind="sharing",
        text=long_text,
        quote=long_text,
        game=game,
        save_id=save_id,
        save_name="主世界",
        evidence_game_chat_id=gc_id,
        created_session_id=old_session,
        now_ms_value=_NOW - 79_000_000,
    )

    # 模拟 80 字截断后的常驻规矩文本（带类别前缀与省略号）
    prefix = "共享惯例："
    truncated_rule = (prefix + long_text)[: 80 - len(ellipsis)] + ellipsis
    assert len(truncated_rule) == 80

    provider = _passive(store, current_session, _Clock(), social_fact_repo=store.social_facts)
    assert (
        await provider.cues_for_game_lead(
            game=game,
            texts=("去主仓库拿附魔书",),
            exclude_texts=(truncated_rule,),
            current_saves=saves,
            limit=2,
        )
        == []
    )

    # 极短截断前缀（不足 12 字）不得误杀长事实
    assert await provider.cues_for_game_lead(
        game=game,
        texts=("去主仓库拿附魔书",),
        exclude_texts=(f"共享惯例：西侧主仓库{ellipsis}",),
        current_saves=saves,
        limit=2,
    ) == [f"[存档记忆·共享惯例] {long_text}"]


@pytest.mark.parametrize(
    "chatter",
    [
        "今天天气真好随便逛逛看看附近",
        "我准备去前面看一眼",
        "嗯好的马上来",
        "准备盖一座高塔",
        "帮我整理一下东西吧",
        "ok hello good",
        "挖点石头",
        "知道了",
        "没问题",
        "明白",
        "收到",
        "小心点",
        "等等",
        "不管了",
        "回营地",
        "去挖矿",
        "砍点木头",
    ],
)
async def test_game_lead_intent_returns_empty_for_irrelevant_or_generic_chatter(
    store: SQLiteDatabase, chatter: str
) -> None:
    """无关闲聊、日常口头禅与高频基础泛词严格返回空列表（0 噪声），绝不误触发无关事实或档案。"""
    old_session = await store.sessions.insert_live_session(started_at_ms=_NOW - 86_400_000)
    await store.sessions.close_live_session(live_session_id=old_session, ended_at_ms=_NOW - 80_000_000)
    current_session = await _session(store)

    game = "block_game"
    save_id = "save_alpha"
    saves = {game: GameSave(id=save_id, name="主世界")}

    for text in (
        "营地本来就有一座现成的下界门",
        "营地东边已经建好了自动刷石机",
        "目前准备一起修一条通往矿洞的铁路",
        "大家平时共用整理好的箱子放东西",
        "PlayerA 在北岸修了一座石头城堡",
        "下界传送门修在北边山脚下，用石头围起来了",
        "知道了大家的问题，小心点挖矿别掉岩浆",
    ):
        gc_id = await _insert_game_chat_evidence(
            store, live_session_id=old_session, game=game, save_id=save_id, content=text
        )
        await store.social_facts.insert_fact(
            kind="works",
            text=text,
            quote=text,
            game=game,
            save_id=save_id,
            save_name="主世界",
            evidence_game_chat_id=gc_id,
            created_session_id=old_session,
            now_ms_value=_NOW - 79_000_000,
        )
    await store.archive.insert_line(
        live_session_id=old_session,
        text="今天在北岸修好了水车",
        quote="今天终于把水车修好了，明白流程就没问题了",
        evidence_chat_id=1,
        game=game,
        save_id=save_id,
        save_name="主世界",
    )

    indexer = MemoryIndexer(repo=store.memory_index, embedding=None, embedding_note="test-no-vec")
    await indexer.run_once()
    search = MemorySearch(repo=store.memory_index, embedding=None)
    provider = _passive(store, current_session, _Clock(), social_fact_repo=store.social_facts, search=search)

    assert (
        await provider.cues_for_game_lead(
            game=game,
            texts=(chatter,),
            current_saves=saves,
            limit=2,
        )
        == []
    )


@pytest.mark.parametrize(
    ("intent_text", "expected_snippet"),
    [
        ("凑钻石镐去砌下界门", "营地本来就有一座现成的下界门"),
        ("修一下刷石机", "营地东边已经建好了自动刷石机"),
        ("去北岸看水车转没转", "今天在北岸修好了水车"),
    ],
)
async def test_game_lead_intent_preserves_recall_for_real_action_intents(
    store: SQLiteDatabase,
    intent_text: str,
    expected_snippet: str,
) -> None:
    """收紧泛词过滤后，真实实质行动意图（下界门/刷石机/水车）依然 100% 精准命中对应记忆。"""
    old_session = await store.sessions.insert_live_session(started_at_ms=_NOW - 86_400_000)
    await store.sessions.close_live_session(live_session_id=old_session, ended_at_ms=_NOW - 80_000_000)
    current_session = await _session(store)

    game = "block_game"
    save_id = "save_alpha"
    saves = {game: GameSave(id=save_id, name="主世界")}

    for text in (
        "营地本来就有一座现成的下界门",
        "营地东边已经建好了自动刷石机",
    ):
        gc_id = await _insert_game_chat_evidence(
            store, live_session_id=old_session, game=game, save_id=save_id, content=text
        )
        await store.social_facts.insert_fact(
            kind="works",
            text=text,
            quote=text,
            game=game,
            save_id=save_id,
            save_name="主世界",
            evidence_game_chat_id=gc_id,
            created_session_id=old_session,
            now_ms_value=_NOW - 79_000_000,
        )
    await store.archive.insert_line(
        live_session_id=old_session,
        text="今天在北岸修好了水车",
        quote="今天终于把水车修好了",
        evidence_chat_id=1,
        game=game,
        save_id=save_id,
        save_name="主世界",
    )

    provider = _passive(store, current_session, _Clock(), social_fact_repo=store.social_facts)
    cues = await provider.cues_for_game_lead(
        game=game,
        texts=(intent_text,),
        current_saves=saves,
        limit=2,
    )
    assert any(expected_snippet in item for item in cues)
