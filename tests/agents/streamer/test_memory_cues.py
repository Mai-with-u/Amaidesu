"""线索推送：承诺卡、老观众在场一行与遗忘命令回执行。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest

from src.agents.streamer.memory.cues import (
    MAX_CARDS_PER_BATCH,
    MAX_PUSHES_PER_CARD,
    PUSH_COOLDOWN_MS,
    MemoryCueProvider,
)
from src.modules.events.payloads.room import RoomMessageUser
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.privacy import PrivacyService
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
# 承诺卡
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


async def test_game_commitment_card_only_while_that_game_is_open(store: SQLiteDatabase) -> None:
    session_id = await _session(store)
    await store.commitments.insert_commitment(what="继续炸 TNT", quote="下次接着炸 TNT", game="Minecraft")
    provider = _provider(store, session_id=session_id)

    closed = await provider.collect(batch=[_message()])
    assert closed.card_count == 0
    opened = await provider.collect(batch=[_message()], open_games=["Minecraft"])
    assert opened.card_count == 1
    # 卡面点名编号、游戏与她的原话；不写"现在 X 开着"——open_games 是已启用的工具面，
    # 不代表主播此刻正在玩哪一个
    assert "[约#" in opened.text and "Minecraft" in opened.text and "下次接着炸 TNT" in opened.text


async def test_cards_are_capped_per_batch(store: SQLiteDatabase) -> None:
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


async def test_game_cards_only_for_the_current_save(store: SQLiteDatabase) -> None:
    """眼下在新档：老档里答应的不推；新档里答应的照推；没记存档的照推并注明不确定。"""
    session_id = await _session(store)
    await store.commitments.insert_commitment(what="修北边的桥", quote="桥下次修完", game="minecraft", save_id="a")
    here = await store.commitments.insert_commitment(
        what="去村里换绿宝石", quote="下回去村里换绿宝石", game="minecraft", save_id="b"
    )
    provider = _provider(store, session_id=session_id)
    current = {"minecraft": GameSave(id="b", name="新档")}

    cues = await provider.collect(batch=[_message()], open_games=["minecraft"], current_saves=current)
    assert cues.commitment_ids == (here,)
    assert "桥下次修完" not in cues.text

    await store.execute("UPDATE commitments SET status='closed' WHERE id=?", (here,))
    unsure = await store.commitments.insert_commitment(what="把仓库理一遍", quote="仓库我下次理", game="minecraft")
    cues = await provider.collect(batch=[_message()], open_games=["minecraft"], current_saves=current)
    assert cues.commitment_ids == (unsure,)
    assert "不确定是在哪个存档里答应的" in cues.text


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
# 被动想起：规则与地点两路的档案卡、老梗卡
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
    """弹幕提到触发词：推那行档案（带 档#N，写为什么想起来），编号进点名名单，记一行线索卡记录。"""
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
    """档案卡被她点名用上（档#N 进了台词的记忆引用），本场不再推，记录回填用上的时刻。"""
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
    """只由她自己台词命中的卡（自触发）每条每场只推一次；观众弹幕命中的照旧两次。"""
    session_id = await _session(store)
    line_id = await _old_line(store)
    clock = _Clock()
    provider = _passive(store, session_id, clock)
    await _her_line(store, session_id, "说起来团子最近怎么样了")

    first = await provider.collect(batch=[])
    assert first.archive_ids == (line_id,) and "想起它是因为你刚说到「团子」" in first.text
    assert first.asked_archive_ids == (), "只由她自己的台词想起的不算有人提到"
    clock.value += PUSH_COOLDOWN_MS + 1
    assert (await provider.collect(batch=[])).archive_ids == (), "自触发一场只推一次"

    clock.value += PUSH_COOLDOWN_MS + 1
    assert (await provider.collect(batch=[_message(content="团子！")])).archive_ids == (line_id,), "观众提到照推"
    entries = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert [(entry.self_triggered, entry.sources) for entry in entries] == [
        (True, ["self"]),
        (False, ["danmaku", "self"]),
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
    """观众弹幕提到老梗栏里的梗或别名：推一张老梗卡；她自己说到不推（老梗栏写明别自己翻出来）。"""
    session_id = await _session(store)
    jokes = "倒立洗头/洗头挑战：她说过输了就倒立洗头"
    await _her_line(store, session_id, "今天不搞倒立洗头")
    provider = _passive(store, session_id, _Clock(), inside_jokes=jokes)

    assert (await provider.collect(batch=[])).text == ""
    cues = await provider.collect(batch=[_message(content="洗头挑战什么时候", message_id="m3")])
    assert "[老梗] 弹幕 m3 提到了「倒立洗头」" in cues.text
    (entry,) = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert entry.card_kind == "joke" and entry.detail == {"keyword_index": [1], "how": ["exact"], "joke_index": 0}
