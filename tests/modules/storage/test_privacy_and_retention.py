"""阶段 2 隐私、遗忘命令、宽限删除、遮名、来访计数、删除函数与保留期清理测试。"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.storage_schemas import DEFAULT_FORGET_PHRASES, DEFAULT_RETRACT_PHRASES, StorageRootConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.interceptors.session_stamp import SessionStampInterceptor
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessageUser
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import UtteranceDroppedPayload
from src.modules.session import LiveSessionManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.privacy import GRACE_DELETE_MAX_MS, PrivacyService, matches_phrase_table
from src.modules.storage.bootstrap import build_privacy_and_retention
from src.modules.storage.retention import RetentionService
from src.modules.storage.storage_ledger import StorageLedger
from src.modules.utils.time_utils import now_ms
from tests.helpers import FakeClock
from tests.modules.storage.helpers import make_room_message


def _advance(clock: FakeClock, delta_ms: int) -> int:
    """推进假时钟并返回推进后的时刻（供以时刻作载荷字段的行内调用）。"""
    clock.tick(delta_ms)
    return clock.now_ms()


async def _emit(bus: EventBus, event: str, payload: Any) -> None:
    await bus.emit(event, payload, source="test")
    await bus.wait_until_drained()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("忘了我", True),
        ("忘了我吧，别记我了", True),
        ("主播忘了我吧", True),
        ("麦麦，忘了我", True),
        ("@麦麦 忘了我吧", True),
        ("@路人甲 忘了我", True),
        ("@麦麦忘了我", True),  # @ 与正文粘连：剥掉 @+称呼后仍剩完整短语
        ("忘了我吧[微笑]", True),  # B 站文字表情码只作分隔，不算进短语
        ("[doge]忘了我", True),
        ("忘了我吧😭", True),
        ("忘了我吧~", True),
        ("忘了我吧３", False),  # 全角数字算词内字符（Unicode 意义），不再当分隔符切掉
        ("忘了我ね", False),  # 假名同理：整句不是纯短语
        ("别忘了我", False),
        ("你会不会忘了我呀", False),
        ("忘了我吧哈哈", False),
        ("把我的记录都删了吧", False),
        ("主播", False),
        ("😭~！", False),
        ("", False),
    ],
)
def test_forget_phrase_matching(text: str, expected: bool) -> None:
    assert matches_phrase_table(text, DEFAULT_FORGET_PHRASES, bot_name="麦麦") is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("开玩笑的", True),
        ("主播算了吧", True),
        ("麦麦，别忘了我😭", True),
        ("记住我", True),
        ("开玩笑的哈哈", False),
        ("你算了吧你", False),
    ],
)
def test_retract_phrase_matching(text: str, expected: bool) -> None:
    assert matches_phrase_table(text, DEFAULT_RETRACT_PHRASES, bot_name="麦麦") is expected


async def test_key_file_auto_generated_when_empty_and_refuses_boot_when_missing_with_entries(
    store: SQLiteDatabase, tmp_path: Path
) -> None:
    key_path = tmp_path / "secrets" / "forget.key"
    privacy = PrivacyService(store.manager, db_path=store.db_path, key_path=key_path, bot_name="麦麦")
    await privacy.initialize()
    assert key_path.exists() and len(key_path.read_bytes()) == 32

    # 写入一条不记录名单后删掉密钥文件：再次启动必须硬报错并给出两条出路
    await privacy.process_incoming_viewer_message(
        platform="bilibili",
        id_kind="account",
        external_id="u_secret_1",
        user_name="测试者",
        message_type="danmaku",
        content="忘了我",
    )
    assert await privacy.count_forget_list() == 1
    await privacy.close()

    key_path.unlink()
    privacy2 = PrivacyService(store.manager, db_path=store.db_path, key_path=key_path, bot_name="麦麦")
    with pytest.raises(RuntimeError, match="DELETE FROM forget_list"):
        await privacy2.initialize()


async def test_ledger_speech_utterance_dropped_and_formal_visit_counts(
    store: SQLiteDatabase, event_bus: EventBus, tmp_path: Path, loguru_capture
) -> None:
    clock = FakeClock()
    privacy = PrivacyService(
        store.manager, db_path=store.db_path, key_path=tmp_path / "forget.key", bot_name="麦麦", clock=clock.now_ms
    )
    await privacy.initialize()
    retention = RetentionService(store.manager, privacy, db_path=store.db_path, clock=clock.now_ms)
    manager = LiveSessionManager(store.sessions, store.chat, event_bus, platform="bilibili", clock=clock.now_ms)
    await manager.start()
    event_bus.add_interceptor(SessionStampInterceptor(manager))
    ledger = StorageLedger(
        event_bus=event_bus,
        chat_repo=store.chat,
        viewer_repo=store.viewers,
        event_repo=store.events,
        session_manager=manager,
        privacy=privacy,
        retention=retention,
    )
    await ledger.start()
    try:
        # 正式场 1：阿杰发两条真弹幕 + 一条模拟弹幕，只计 1 场
        s1 = await manager.open_session(kind="live")
        ajie = RoomMessageUser(id="10086", name="阿杰")
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=ajie, content="第一条", timestamp_ms=clock.now_ms()),
        )
        clock.tick(5_000)
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=ajie, content="第二条", timestamp_ms=clock.now_ms()),
        )
        # 主播发两句台词，其中第 2 句随后收到 dropped 事件；另发一条找不到行的 dropped
        await _emit(
            event_bus,
            CoreEvents.STREAMER_SPEECH,
            StreamerSpeechPayload(
                utterance_id="utt_ok",
                text="这句送到了",
                emotion="happy",
                emotion_intensity=0.6,
                memory_refs=["约#7"],
                timestamp_ms=clock.now_ms(),
            ),
        )
        await _emit(
            event_bus,
            CoreEvents.STREAMER_SPEECH,
            StreamerSpeechPayload(
                utterance_id="utt_drop",
                text="这句被关停清掉",
                emotion="neutral",
                emotion_intensity=0.5,
                timestamp_ms=clock.now_ms(),
            ),
        )
        await _emit(
            event_bus,
            CoreEvents.TTS_UTTERANCE_DROPPED,
            UtteranceDroppedPayload(utterance_id="utt_drop", reason="queue_stopped", timestamp_ms=clock.now_ms() + 10),
        )
        await _emit(
            event_bus,
            CoreEvents.TTS_UTTERANCE_DROPPED,
            UtteranceDroppedPayload(utterance_id="utt_missing", reason="expired", timestamp_ms=clock.now_ms() + 20),
        )
        await manager.close_session()
        await event_bus.wait_until_drained()

        # 彩排场：阿杰发言不增加正式场来访数
        clock.tick(60_000)
        await manager.open_session(kind="rehearsal")
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=ajie, content="彩排发言", timestamp_ms=clock.now_ms()),
        )
        last_rehearsal_ms = clock.now_ms()
        await manager.close_session()
        await event_bus.wait_until_drained()

        # 正式场 2：阿杰再次发言，sessions_count 1->2，prev_seen_ms 记下本场前最后活跃时刻
        clock.tick(120_000)
        s3 = await manager.open_session(kind="live")
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=ajie, content="又来啦", timestamp_ms=clock.now_ms()),
        )
        await manager.close_session()
        await event_bus.wait_until_drained()

        speeches = {
            r["utterance_id"]: dict(r)
            for r in await store.execute("SELECT * FROM live_chat WHERE sender_role='assistant'")
        }
        assert speeches["utt_ok"]["dropped_at_ms"] is None
        assert speeches["utt_ok"]["memory_refs"] == '["约#7"]'
        assert speeches["utt_drop"]["dropped_at_ms"] is not None

        (vrow,) = await store.execute("SELECT * FROM viewers WHERE account_id='10086'")
        assert (vrow["sessions_count"], vrow["last_session_id"], vrow["prev_seen_ms"]) == (2, s3, last_rehearsal_ms)
        assert s1 != s3
        assert any("没送达标记找不到台词行次数=1" in r["message"] for r in loguru_capture.records)
    finally:
        await ledger.stop()
        await privacy.close()


async def test_grace_deletion_retraction_masking_and_anonymized_payments(
    store: SQLiteDatabase, event_bus: EventBus, tmp_path: Path
) -> None:
    clock = FakeClock()
    privacy = PrivacyService(
        store.manager, db_path=store.db_path, key_path=tmp_path / "forget.key", bot_name="麦麦", clock=clock.now_ms
    )
    await privacy.initialize()
    retention = RetentionService(store.manager, privacy, db_path=store.db_path, clock=clock.now_ms)
    manager = LiveSessionManager(store.sessions, store.chat, event_bus, platform="bilibili", clock=clock.now_ms)
    await manager.start()
    event_bus.add_interceptor(SessionStampInterceptor(manager))
    ledger = StorageLedger(
        event_bus=event_bus,
        chat_repo=store.chat,
        viewer_repo=store.viewers,
        event_repo=store.events,
        session_manager=manager,
        privacy=privacy,
        retention=retention,
    )
    await ledger.start()
    try:
        # 1. 开播前发"忘了我"（无进行中场次）：立即进不记录名单
        pre_user = RoomMessageUser(id="u_pre", name="前排哥")
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=pre_user, content="主播忘了我吧", timestamp_ms=clock.now_ms()),
        )
        assert await privacy.is_external_id_forgotten(id_kind="account", external_id="u_pre") is True

        # 2. 开播：u_pre 送礼、发 SC、上舰、发弹幕；主播口误叫了"前排哥"
        await manager.open_session(kind="live")
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=pre_user, content="名单内弹幕不落库", timestamp_ms=_advance(clock, 1000)),
        )
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_GIFT,
            make_room_message(
                user=pre_user, message_type="gift", gift_name="火箭", gift_count=1, timestamp_ms=_advance(clock, 1000)
            ),
        )
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
            make_room_message(
                user=pre_user,
                message_type="super_chat",
                content="秘密留言",
                sc_amount=30.0,
                timestamp_ms=_advance(clock, 1000),
            ),
        )
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_SPEECH
            if hasattr(CoreEvents, "ROOM_MESSAGE_SPEECH")
            else CoreEvents.STREAMER_SPEECH,
            StreamerSpeechPayload(
                utterance_id="utt_mask_1",
                text="谢谢前排哥的火箭！",
                emotion="happy",
                emotion_intensity=0.8,
                timestamp_ms=_advance(clock, 1000),
            ),
        )

        # 断言：u_pre 没有观众行、没有弹幕；礼物和 SC 保留金额但身份与留言全空；主播台词已遮名
        assert await store.execute("SELECT * FROM viewers WHERE account_id='u_pre'") == []
        assert await store.execute("SELECT * FROM live_chat WHERE sender_id='u_pre'") == []
        (gift_row,) = await store.execute("SELECT * FROM gifts")
        assert (gift_row["user_id"], gift_row["user_name"], gift_row["viewer_id"], gift_row["total_price"]) == (
            "",
            "",
            None,
            1000,
        )
        (sc_row,) = await store.execute("SELECT * FROM super_chats")
        assert (sc_row["user_id"], sc_row["user_name"], sc_row["message"], sc_row["total_price"]) == (
            "",
            "",
            "",
            30_000,
        )
        (speech_row,) = await store.execute("SELECT content FROM live_chat WHERE utterance_id='utt_mask_1'")
        assert speech_row["content"] == "谢谢一位观众的火箭！"

        # 3. 回放模拟弹幕里的"忘了我"不触发命令
        real_user = RoomMessageUser(id="u_ajie", name="阿杰")
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=real_user, content="大家好", timestamp_ms=_advance(clock, 1000)),
        )
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=real_user, content="忘了我", simulated=True, timestamp_ms=_advance(clock, 1000)),
        )
        assert await privacy.is_external_id_forgotten(id_kind="account", external_id="u_ajie") is False

        # 4. 宽限期内撤回：阿杰发"忘了我吧😭"，随后发一条中间弹幕（不落库），再发"开玩笑的"（撤回并落库）
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(
                user=real_user, content="忘了我吧😭", message_id="m_f1", timestamp_ms=_advance(clock, 1000)
            ),
        )
        (ajie_pending,) = await store.execute("SELECT * FROM viewers WHERE account_id='u_ajie'")
        assert ajie_pending["forget_requested_at_ms"] is not None
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=real_user, content="中间这句不记", timestamp_ms=_advance(clock, 1000)),
        )
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(
                user=real_user, content="开玩笑的", message_id="m_r1", timestamp_ms=_advance(clock, 1000)
            ),
        )
        assert privacy.was_retracted_in_session(message_id="m_r1") is True
        (ajie_restored,) = await store.execute("SELECT * FROM viewers WHERE account_id='u_ajie'")
        assert ajie_restored["forget_requested_at_ms"] is None
        ajie_msgs = [
            r["content"]
            for r in await store.execute(
                "SELECT content FROM live_chat WHERE sender_id='u_ajie' AND simulated=0 ORDER BY id"
            )
        ]
        assert ajie_msgs == ["大家好", "开玩笑的"]

        # 5. 另一位观众小林发"忘了我"，不撤回 → 下播后自动执行删除；下播后遮名集合清空
        xiaolin = RoomMessageUser(id="u_lin", name="小林")
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=xiaolin, content="我来过", timestamp_ms=_advance(clock, 1000)),
        )
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=xiaolin, content="忘了我", timestamp_ms=_advance(clock, 1000)),
        )
        clock.tick(10_000)
        await manager.close_session()
        await event_bus.wait_until_drained()

        # 小林已被删除；删除执行后再发"开玩笑的"无效且记入 late_retractions
        assert await store.execute("SELECT * FROM viewers WHERE account_id='u_lin'") == []
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=xiaolin, content="开玩笑的", timestamp_ms=_advance(clock, 1000)),
        )
        assert privacy.get_stats().late_retractions == 1

        # 6. 下播后阿杰才发"忘了我"：未满 24h 前还在库里，推进满 24h 后清理即删除
        await _emit(
            event_bus,
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            make_room_message(user=real_user, content="忘了我", timestamp_ms=_advance(clock, 5_000)),
        )
        clock.tick(GRACE_DELETE_MAX_MS - 1_000)
        await retention.run_cleanup()
        assert len(await store.execute("SELECT id FROM viewers WHERE account_id='u_ajie'")) == 1
        clock.tick(2_000)
        await retention.run_cleanup()
        assert await store.execute("SELECT id FROM viewers WHERE account_id='u_ajie'") == []
    finally:
        await ledger.stop()
        await privacy.close()


async def test_delete_viewer_erases_bytes_from_db_wal_and_scopes_short_names(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "data" / "amaidesu.db"
    store = SQLiteDatabase(db_file)
    await store.initialize()
    privacy = PrivacyService(store.manager, db_path=db_file, key_path=tmp_path / "secrets" / "forget.key")
    await privacy.initialize()
    try:
        # 场次 1（1000..2000）：目标观众用昵称"小雨"和单字曾用名"风"出现；场次 2（5000..6000）：目标观众未出现，主播说日常词"小雨"
        s1 = await store.sessions.insert_live_session(started_at_ms=1000, kind="live")
        await store.sessions.close_live_session(live_session_id=s1, ended_at_ms=2000)
        s2 = await store.sessions.insert_live_session(started_at_ms=5000, kind="live")
        await store.sessions.close_live_session(live_session_id=s2, ended_at_ms=6000)

        res = await store.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="UID_SECRET_999",
            user_name="风",
            timestamp_ms=1100,
            name_match_window_ms=0,
        )
        vid = res.viewer_id
        # 改名为"小雨"（使用期 1200..1800），并关联 open_id
        await store.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="UID_SECRET_999",
            user_name="小雨",
            timestamp_ms=1200,
            name_match_window_ms=0,
        )
        await store.execute(
            "UPDATE viewers SET open_id='OPEN_SECRET_888', open_app_id='app1' WHERE id=?",
            (vid,),
        )
        await store.execute(
            "UPDATE viewer_names SET last_seen_ms=1800 WHERE viewer_id=? AND user_name='小雨'",
            (vid,),
        )

        # 造同外部 ID 的模拟回放副本观众与弹幕
        sim_res = await store.viewers.resolve_viewer(
            platform="",
            id_kind="account",
            external_id="UID_SECRET_999",
            user_name="小雨",
            timestamp_ms=1300,
            simulated=True,
            name_match_window_ms=0,
        )
        await store.chat.insert_live_chat(
            live_session_id=s1,
            timestamp_ms=1250,
            sender_role="viewer",
            sender_id="UID_SECRET_999",
            sender_name="小雨",
            content="真实弹幕内容_UNIQUE",
            message_type="danmaku",
            viewer_id=vid,
            id_kind="account",
        )
        await store.chat.insert_live_chat(
            live_session_id=s1,
            timestamp_ms=1300,
            sender_role="viewer",
            sender_id="UID_SECRET_999",
            sender_name="小雨",
            content="回放弹幕内容_UNIQUE",
            message_type="danmaku",
            simulated=True,
            viewer_id=sim_res.viewer_id,
            id_kind="account",
        )
        # 付费三表
        await store.chat.insert_gift(
            live_session_id=s1,
            timestamp_ms=1400,
            user_id="UID_SECRET_999",
            user_name="小雨",
            gift_name="礼花",
            quantity=2,
            total_price=2000,
            raw_data='{"uid":"UID_SECRET_999","uname":"小雨"}',
            viewer_id=vid,
            id_kind="account",
        )
        await store.chat.insert_super_chat(
            live_session_id=s1,
            timestamp_ms=1450,
            user_id="UID_SECRET_999",
            user_name="小雨",
            message="SC里的私密话_UNIQUE",
            total_price=30000,
            raw_data='{"uid":"UID_SECRET_999"}',
            viewer_id=vid,
            id_kind="account",
        )
        # 场次 1 主播台词、话题、时间线摘要
        await store.chat.insert_live_chat(
            live_session_id=s1,
            timestamp_ms=1500,
            sender_role="assistant",
            content="欢迎小雨，今天风好大呀！",
            message_type="speak",
        )
        await store.chat.insert_live_chat(
            live_session_id=s2,
            timestamp_ms=5500,
            sender_role="assistant",
            content="外面下小雨啦，真凉快",
            message_type="speak",
        )
        await store.execute(
            "INSERT INTO topics(live_session_id, label, source, score, trend, duration_ms, count)"
            " VALUES (?, '小雨点歌环节', 'llm', 1.0, 0.0, 1000, 1)",
            (s1,),
        )
        await store.execute(
            "INSERT INTO timeline_summary(live_session_id, start_ms, end_ms, summary)"
            " VALUES (?, 1000, 2000, '小雨进场互动')",
            (s1,),
        )

        assert await privacy.delete_viewer(viewer_id=vid, add_to_forget_list=True) is True

        # 1) 短昵称撞词检查：场次 1 的"小雨"被替换，单字曾用名"风"不误伤"今天风好大"，场次 2 的"外面下小雨啦"不被误伤
        s1_speech = (
            await store.execute(
                "SELECT content FROM live_chat WHERE live_session_id=? AND sender_role='assistant'", (s1,)
            )
        )[0]["content"]
        s2_speech = (
            await store.execute(
                "SELECT content FROM live_chat WHERE live_session_id=? AND sender_role='assistant'", (s2,)
            )
        )[0]["content"]
        assert s1_speech == "欢迎一位观众，今天风好大呀！"
        assert s2_speech == "外面下小雨啦，真凉快"

        # 2) 关闭连接后对 .db 与 .db-wal 做字节级残留检查（旧记忆目录已由 v25 迁移整体删除）
        await privacy.close()
        await store.close()

        forbidden_tokens = [
            b"UID_SECRET_999",
            b"OPEN_SECRET_888",
            "真实弹幕内容_UNIQUE".encode("utf-8"),
            "回放弹幕内容_UNIQUE".encode("utf-8"),
            "SC里的私密话_UNIQUE".encode("utf-8"),
        ]
        for candidate in [db_file, Path(str(db_file) + "-wal")]:
            if candidate.exists():
                raw = candidate.read_bytes()
                for token in forbidden_tokens:
                    assert token not in raw, f"{candidate.name} 残留敏感字节: {token!r}"
    finally:
        if store.initialized:
            await privacy.close()
            await store.close()


async def test_retention_cleanup_respects_zero_defaults_and_cleans_expired_items(tmp_path: Path) -> None:
    clock = FakeClock(100 * 86_400_000)
    db_file = tmp_path / "data" / "amaidesu.db"
    store = SQLiteDatabase(db_file)
    await store.initialize()
    privacy = PrivacyService(store.manager, db_path=db_file, key_path=tmp_path / "forget.key", clock=clock.now_ms)
    await privacy.initialize()
    try:
        old_end = clock.now_ms() - 10 * 86_400_000
        recent_end = clock.now_ms() - 2 * 86_400_000
        s_old = await store.sessions.insert_live_session(started_at_ms=old_end - 3600_000, kind="live")
        await store.sessions.close_live_session(live_session_id=s_old, ended_at_ms=old_end)
        s_new = await store.sessions.insert_live_session(started_at_ms=recent_end - 3600_000, kind="live")
        await store.sessions.close_live_session(live_session_id=s_new, ended_at_ms=recent_end)

        # 老观众（40 天未活跃）与近期观众（2 天前活跃）
        v_absent = (
            await store.viewers.resolve_viewer(
                platform="bilibili",
                id_kind="account",
                external_id="U_ABSENT_40D",
                user_name="远古路人",
                timestamp_ms=clock.now_ms() - 40 * 86_400_000,
                name_match_window_ms=0,
            )
        ).viewer_id
        v_active = (
            await store.viewers.resolve_viewer(
                platform="bilibili",
                id_kind="account",
                external_id="U_ACTIVE_2D",
                user_name="常驻观众",
                timestamp_ms=recent_end,
                name_match_window_ms=0,
            )
        ).viewer_id

        await store.chat.insert_live_chat(
            live_session_id=s_old,
            timestamp_ms=old_end - 1000,
            sender_role="viewer",
            sender_id="U_ACTIVE_2D",
            sender_name="常驻观众",
            content="过期观众弹幕_EXPIRED_CHAT",
            message_type="danmaku",
            viewer_id=v_active,
        )
        await store.chat.insert_live_chat(
            live_session_id=s_old,
            timestamp_ms=old_end - 500,
            sender_role="assistant",
            content="主播自己的台词长期保留",
            message_type="speak",
        )
        await store.chat.insert_live_chat(
            live_session_id=s_new,
            timestamp_ms=recent_end - 1000,
            sender_role="viewer",
            sender_id="U_ACTIVE_2D",
            sender_name="常驻观众",
            content="近期观众弹幕保留",
            message_type="danmaku",
            viewer_id=v_active,
        )
        await store.chat.insert_super_chat(
            live_session_id=s_old,
            timestamp_ms=old_end - 800,
            user_id="U_ACTIVE_2D",
            user_name="常驻观众",
            message="过期SC留言_EXPIRED_SC",
            total_price=50000,
            raw_data='{"raw":"EXPIRED_RAW_JSON"}',
            viewer_id=v_active,
        )
        await store.execute(
            "INSERT INTO topics(live_session_id, label, source, score, trend, duration_ms, count)"
            " VALUES (?, '过期话题_EXPIRED_TOPIC', 'llm', 1.0, 0.0, 1000, 1)",
            (s_old,),
        )
        await store.execute(
            "INSERT INTO llm_requests(request_id, timestamp_ms, profile_name, model_name, response_content, prompt_tokens, preview_text)"
            " VALUES ('req_old', ?, 'planner', 'm', '过期模型响应_EXPIRED_LLM', 123, '过期预览_EXPIRED_PREV')",
            (old_end,),
        )

        backup_dir = db_file.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        # 备份全族按条目修改时刻统一参与保留期：迁移前整库快照、普通整库备份、迁移时移入的旧记忆目录
        old_migration_backup = backup_dir / "amaidesu-pre-v24-old.db"
        new_migration_backup = backup_dir / "amaidesu-pre-v25-new.db"
        old_plain_backup = backup_dir / "amaidesu-2025-01-01.db"
        new_plain_backup = backup_dir / "amaidesu-2025-01-09.db"
        for path, content in (
            (old_migration_backup, b"old-migration"),
            (new_migration_backup, b"new-migration"),
            (old_plain_backup, b"old-plain"),
            (new_plain_backup, b"new-plain"),
        ):
            path.write_bytes(content)
        old_memory_backup = backup_dir / "memory-pre-v25-20250101-000000000000"
        new_memory_backup = backup_dir / "memory-pre-v25-20250109-000000000000"
        old_memory_backup.mkdir()
        new_memory_backup.mkdir()
        (old_memory_backup / "MEMORY.md").write_text("旧笔记", encoding="utf-8")
        (new_memory_backup / "MEMORY.md").write_text("新笔记", encoding="utf-8")
        # 备份目录里的其它文件不归保留期管
        unrelated_file = backup_dir / "notes.txt"
        unrelated_file.write_text("手工笔记", encoding="utf-8")
        for path in (old_migration_backup, old_plain_backup, old_memory_backup):
            os.utime(path, (old_end / 1000.0, old_end / 1000.0))
        for path in (new_migration_backup, new_plain_backup, new_memory_backup, unrelated_file):
            os.utime(path, (recent_end / 1000.0, recent_end / 1000.0))

        # 1) 默认 0 天什么都不删
        noop_retention = RetentionService(
            store.manager, privacy, db_path=db_file, raw_days=0, viewer_absence_days=0, clock=clock.now_ms
        )
        noop_res = await noop_retention.run_cleanup()
        assert noop_res.chat_deleted == 0
        assert old_plain_backup.exists() and old_migration_backup.exists() and old_memory_backup.is_dir()

        # 2) 配置 raw_days=7, viewer_absence_days=30 执行清理
        active_retention = RetentionService(
            store.manager, privacy, db_path=db_file, raw_days=7, viewer_absence_days=30, clock=clock.now_ms
        )
        res = await active_retention.run_cleanup()
        assert res.chat_deleted == 1
        assert res.payments_scrubbed == 1
        assert res.summaries_deleted == 1
        assert res.llm_bodies_scrubbed == 1
        # 到期的三类备份都删：普通整库备份、迁移前快照、迁移时移入的旧记忆目录
        assert res.backups_deleted == 3
        assert res.absent_viewers_deleted == 1
        assert not old_plain_backup.exists() and new_plain_backup.exists()
        assert not old_migration_backup.exists() and new_migration_backup.exists()
        assert not old_memory_backup.exists() and new_memory_backup.is_dir()
        assert unrelated_file.exists()

        # 主播台词、近期观众弹幕、SC 金额、LLM token 统计均保留；缺席观众被删但不进 forget_list
        chats = [r["content"] for r in await store.execute("SELECT content FROM live_chat ORDER BY id")]
        assert chats == ["主播自己的台词长期保留", "近期观众弹幕保留"]
        (sc_row,) = await store.execute("SELECT total_price, user_id, message, raw_data FROM super_chats")
        assert (sc_row["total_price"], sc_row["user_id"], sc_row["message"], sc_row["raw_data"]) == (
            50000,
            "",
            "",
            None,
        )
        (llm_row,) = await store.execute("SELECT prompt_tokens, response_content, preview_text FROM llm_requests")
        assert (llm_row["prompt_tokens"], llm_row["response_content"], llm_row["preview_text"]) == (123, None, "")
        assert await store.viewers.get_viewer(viewer_id=v_absent) is None
        assert await privacy.is_external_id_forgotten(id_kind="account", external_id="U_ABSENT_40D") is False
        assert await store.viewers.get_viewer(viewer_id=v_active) is not None

        # 字节级验证到期内容在 .db 与 -wal 中均不残留
        await privacy.close()
        await store.close()
        for candidate in [db_file, Path(str(db_file) + "-wal")]:
            if candidate.exists():
                raw = candidate.read_bytes()
                for expired in (
                    "过期观众弹幕_EXPIRED_CHAT",
                    "过期SC留言_EXPIRED_SC",
                    "EXPIRED_RAW_JSON",
                    "过期话题_EXPIRED_TOPIC",
                    "过期模型响应_EXPIRED_LLM",
                    "过期预览_EXPIRED_PREV",
                    "U_ABSENT_40D",
                    "远古路人",
                ):
                    assert expired.encode("utf-8") not in raw, f"{candidate.name} 残留到期内容: {expired}"
    finally:
        if store.initialized:
            await privacy.close()
            await store.close()


def test_storage_toml_materializes_retention_and_privacy_defaults(tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    merged, _drift = load_config_dir(tmp_path)
    storage_doc = merged["storage"]
    root = StorageRootConfig.from_dict({k: storage_doc[k] for k in ("sqlite", "viewers", "retention", "privacy")})
    assert root.retention.raw_days == 0 and root.retention.viewer_absence_days == 0
    assert root.privacy.key_path == "secrets/forget.key"
    assert "忘了我" in root.privacy.forget_phrases and "开玩笑的" in root.privacy.retract_phrases


async def test_delete_viewer_warns_when_wal_checkpoint_is_busy(tmp_path: Path, loguru_capture: Any) -> None:
    """删除后截断预写日志被其它连接挡住时：删除照常成功，但打 warning 点名可能残留。"""
    db_file = tmp_path / "data" / "amaidesu.db"
    # 忙等待超时调小：本用例要观察 checkpoint 拿不到锁时的告警，不必等满默认 30 秒
    store = SQLiteDatabase(db_file, timeout=0.2)
    await store.initialize()
    privacy = PrivacyService(store.manager, db_path=db_file, key_path=tmp_path / "forget.key")
    await privacy.initialize()
    reader = sqlite3.connect(db_file)
    try:
        resolution = await store.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="UID_WAL_BUSY",
            user_name="占用连接观众",
            timestamp_ms=1_000,
            name_match_window_ms=0,
        )
        # 另一个连接持有读事务：checkpoint 拿不到锁，PRAGMA 返回 busy=1
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM viewers").fetchone()

        assert await privacy.delete_viewer(viewer_id=resolution.viewer_id, add_to_forget_list=True) is True
        assert await store.viewers.get_viewer(viewer_id=resolution.viewer_id) is None
        warnings = [record for record in loguru_capture.records if record["level"] == "WARNING"]
        assert any("截断预写日志未完成" in record["message"] for record in warnings), (
            f"应记录 checkpoint busy 告警，实际日志: {[record['message'] for record in warnings]}"
        )
    finally:
        reader.rollback()
        reader.close()
        await privacy.close()
        await store.close()


async def test_build_privacy_and_retention_startup_effects_false_skips_key_and_cleanup(tmp_path: Path) -> None:
    """--dry 冒烟装配：不生成密钥文件、不跑启动期保留期清理（过期行原样留着）。"""
    now = now_ms()
    db_file = tmp_path / "data" / "amaidesu.db"
    store = SQLiteDatabase(db_file)
    await store.initialize()
    try:
        session_id = await store.sessions.insert_live_session(started_at_ms=now - 11 * 86_400_000, kind="live")
        await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=now - 10 * 86_400_000)
        resolution = await store.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="U_DRY_SMOKE",
            user_name="冒烟观众",
            timestamp_ms=now - 10 * 86_400_000,
            name_match_window_ms=0,
        )
        await store.chat.insert_live_chat(
            live_session_id=session_id,
            timestamp_ms=now - 10 * 86_400_000,
            sender_role="viewer",
            sender_id="U_DRY_SMOKE",
            sender_name="冒烟观众",
            content="过期弹幕_DRY_SMOKE",
            message_type="danmaku",
            viewer_id=resolution.viewer_id,
            id_kind="account",
        )
    finally:
        await store.close()

    config = {
        "sqlite": {"db_path": str(db_file)},
        "retention": {"raw_days": 7},
        "privacy": {"key_path": str(tmp_path / "secrets" / "dry.key")},
    }
    database = SQLiteDatabase(db_file)
    await database.initialize()
    try:
        privacy, _retention = await build_privacy_and_retention(config, database, startup_effects=False)
        try:
            assert not (tmp_path / "secrets" / "dry.key").exists(), "冒烟装配不应生成密钥文件"
            (row,) = await database.execute("SELECT COUNT(*) AS n FROM live_chat WHERE content='过期弹幕_DRY_SMOKE'")
            assert row["n"] == 1, "冒烟装配不应执行启动期保留期清理"
        finally:
            await privacy.close()

        # 对照：同样的库与配置，startup_effects=True 时清理真的会跑（密钥生成、过期行删除）
        privacy, _retention = await build_privacy_and_retention(config, database, startup_effects=True)
        try:
            assert (tmp_path / "secrets" / "dry.key").exists()
            (row,) = await database.execute("SELECT COUNT(*) AS n FROM live_chat WHERE content='过期弹幕_DRY_SMOKE'")
            assert row["n"] == 0
        finally:
            await privacy.close()
    finally:
        await database.close()


async def test_delete_viewer_scrubs_commitments_archive_and_settle_report(tmp_path: Path) -> None:
    """删除观众时承诺、场次档案、整理报告 JSON 与字节级残留一并处理。"""
    db_file = tmp_path / "data" / "amaidesu.db"
    store = SQLiteDatabase(db_file)
    await store.initialize()
    privacy = PrivacyService(store.manager, db_path=db_file, key_path=tmp_path / "forget.key")
    await privacy.initialize()
    nickname = "隐私测试昵称"
    try:
        session_id = await store.sessions.insert_live_session(started_at_ms=1000, kind="live")
        await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=2000)
        resolution = await store.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="UID_SCRUB_777",
            user_name=nickname,
            timestamp_ms=1100,
            name_match_window_ms=0,
        )
        viewer_id = resolution.viewer_id
        speech = await store.chat.insert_live_chat(
            live_session_id=session_id,
            timestamp_ms=1500,
            sender_role="assistant",
            content=f"欢迎{nickname}，晚上好",
            message_type="speak",
        )
        await store.chat.insert_live_chat(
            live_session_id=session_id,
            timestamp_ms=1600,
            sender_role="viewer",
            sender_id="UID_SCRUB_777",
            sender_name=nickname,
            content="观众弹幕_SCRUB_UNIQUE",
            message_type="danmaku",
            viewer_id=viewer_id,
            id_kind="account",
        )
        # 对 ta 的承诺（随删除整行清掉）与只在台词里提到 ta 的承诺（保留、原话去名）
        await store.commitments.insert_commitment(
            what=f"给{nickname}画头像",
            quote=f"下播给{nickname}画头像",
            viewer_id=viewer_id,
            created_session_id=session_id,
        )
        await store.commitments.insert_commitment(
            what=f"帮{nickname}找歌", quote=f"帮{nickname}找歌", created_session_id=session_id
        )
        await store.archive.insert_line(
            live_session_id=session_id,
            text=f"{nickname}来互动",
            quote=f"欢迎{nickname}，晚上好",
            evidence_chat_id=int(speech),
        )
        settle_report = json.dumps(
            {
                "failure_reason": None,
                "unregistered": [
                    {
                        "kind": "commitment",
                        "what": f"{nickname}说过的事",
                        "quote": f"原话里带{nickname}",
                        "reason": "测试原因",
                    }
                ],
                "input_note": f"台词提到{nickname}，已裁剪",
            },
            ensure_ascii=False,
        )
        await store.execute("UPDATE live_sessions SET settle_report=? WHERE id=?", (settle_report, session_id))

        assert await privacy.delete_viewer(viewer_id=viewer_id, add_to_forget_list=True) is True

        commitments = [dict(row) for row in await store.execute("SELECT * FROM commitments ORDER BY id")]
        assert len(commitments) == 1, "对 ta 的承诺随删除清掉，其余承诺保留"
        survivor = commitments[0]
        assert nickname not in (survivor["what"] or "") and nickname not in (survivor["quote"] or "")
        assert "一位观众" in (survivor["what"] or "") and "一位观众" in (survivor["quote"] or "")
        # 场次档案不按观众删（它不是 ta 的数据），但其中的昵称要替换掉
        (archive_row,) = await store.execute("SELECT text, quote FROM archive_lines")
        assert archive_row["text"] == "一位观众来互动"
        assert archive_row["quote"] == "欢迎一位观众，晚上好"

        (session_row,) = await store.execute("SELECT settle_report FROM live_sessions WHERE id=?", (session_id,))
        report = json.loads(session_row["settle_report"])
        assert report["failure_reason"] is None
        assert report["unregistered"][0]["kind"] == "commitment"
        assert report["unregistered"][0]["reason"] == "测试原因"
        assert nickname not in json.dumps(report, ensure_ascii=False)
        assert "一位观众" in report["unregistered"][0]["what"]
        assert "一位观众" in report["unregistered"][0]["quote"]
        assert "一位观众" in report["input_note"]

        await privacy.close()
        await store.close()
        forbidden = [b"UID_SCRUB_777", nickname.encode("utf-8"), "观众弹幕_SCRUB_UNIQUE".encode("utf-8")]
        for candidate in [db_file, Path(str(db_file) + "-wal")]:
            if candidate.exists():
                raw = candidate.read_bytes()
                for token in forbidden:
                    assert token not in raw, f"{candidate.name} 残留敏感字节: {token!r}"
    finally:
        if store.initialized:
            await privacy.close()
            await store.close()


async def test_delete_viewer_scrubs_game_creature_and_event_text(tmp_path: Path) -> None:
    """ta 出现过的场次里，游戏事件正文与生物死亡说明里的昵称换成代称；别的场次与名册名字不动。

    名册里与 ta 同名的生物（她的狗也叫这个名字）不能被改成"一位观众"：名册跨场次，
    记不得名字是哪一场起的，按名字全表替换会误改无关的生物。
    """
    db_file = tmp_path / "data" / "amaidesu.db"
    store = SQLiteDatabase(db_file)
    await store.initialize()
    privacy = PrivacyService(store.manager, db_path=db_file, key_path=tmp_path / "forget.key")
    await privacy.initialize()
    nickname = "游戏测试昵称"
    try:
        session_id = await store.sessions.insert_live_session(started_at_ms=1000, kind="live")
        await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=2000)
        # ta 没来过、昵称使用期之外的一场
        other_session = await store.sessions.insert_live_session(started_at_ms=50_000, kind="live")
        await store.sessions.close_live_session(live_session_id=other_session, ended_at_ms=60_000)
        resolution = await store.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="UID_GAME_555",
            user_name=nickname,
            timestamp_ms=1100,
            name_match_window_ms=0,
        )
        await store.execute(
            "INSERT INTO game_creatures(game, world_key, creature_key, type, kind, name, nickname, owner,"
            " death_note, died_session_id, updated_at_ms)"
            " VALUES ('minecraft', 'w1', 'uuid:1', 'wolf', 'tamed', ?, ?, '主播', ?, ?, 1)",
            (nickname, nickname, f"{nickname}送的花", session_id),
        )
        await store.execute(
            "INSERT INTO game_creatures(game, world_key, creature_key, type, kind, name, nickname, owner,"
            " death_note, died_session_id, updated_at_ms)"
            " VALUES ('minecraft', 'w1', 'uuid:2', 'cat', 'tamed', ?, ?, '主播', ?, ?, 1)",
            (nickname, nickname, f"{nickname}掉进了岩浆", other_session),
        )
        await store.execute(
            "INSERT INTO game_events(live_session_id, game, event_type, message, timestamp_ms)"
            " VALUES (?, 'minecraft', 'milestone', ?, 1200)",
            (session_id, f"{nickname}的狗生崽了"),
        )
        await store.execute(
            "INSERT INTO game_events(live_session_id, game, event_type, message, timestamp_ms)"
            " VALUES (?, 'minecraft', 'milestone', ?, 55000)",
            (other_session, f"{nickname}（猫）出门了"),
        )

        assert await privacy.delete_viewer(viewer_id=resolution.viewer_id, add_to_forget_list=True) is True

        wolf, cat = await store.execute(
            "SELECT name, nickname, owner, death_note FROM game_creatures ORDER BY creature_key"
        )
        assert wolf["death_note"] == "一位观众送的花", "ta 出现过的那一场写下的死亡说明要替换"
        assert (wolf["name"], wolf["nickname"], wolf["owner"]) == (nickname, nickname, "主播"), "名册名字不动"
        assert cat["death_note"] == f"{nickname}掉进了岩浆", "别的场次的同名内容不误改"
        events = [row["message"] for row in await store.execute("SELECT message FROM game_events ORDER BY id")]
        assert events == ["一位观众的狗生崽了", f"{nickname}（猫）出门了"]
    finally:
        await privacy.close()
        await store.close()


async def test_grace_fallback_timer_deletes_existing_viewer_after_24h(tmp_path: Path) -> None:
    """开播前发令且已有观众行：行上记待删标记，24 小时兜底定时器到点执行删除。

    定时器的等待函数可注入（生产是 asyncio.sleep），测试用假等待把时钟推进满 24 小时，
    不依赖真实等待。
    """
    clock = FakeClock()
    started_calls: list[int] = []
    release_timer = asyncio.Event()

    class _BlockingSleep:
        """记录请求的等待时长并挂起，直到测试放行；时间流逝由假时钟显式推进。"""

        async def __call__(self, seconds: float) -> None:
            started_calls.append(int(seconds * 1000))
            await release_timer.wait()

    db_file = tmp_path / "data" / "amaidesu.db"
    store = SQLiteDatabase(db_file)
    await store.initialize()
    privacy = PrivacyService(
        store.manager,
        db_path=db_file,
        key_path=tmp_path / "forget.key",
        clock=clock.now_ms,
        sleep=_BlockingSleep(),
    )
    await privacy.initialize()
    try:
        resolution = await store.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="U_GRACE_TIMER",
            user_name="宽限定时观众",
            timestamp_ms=clock.now_ms(),
            name_match_window_ms=0,
        )
        viewer_id = resolution.viewer_id

        disposition = await privacy.process_incoming_viewer_message(
            platform="bilibili",
            id_kind="account",
            external_id="U_GRACE_TIMER",
            user_name="宽限定时观众",
            message_type="danmaku",
            content="忘了我",
            timestamp_ms=clock.now_ms(),
        )
        assert disposition == "forget_command"
        (row,) = await store.execute("SELECT forget_requested_at_ms FROM viewers WHERE id=?", (viewer_id,))
        assert row["forget_requested_at_ms"] == clock.now_ms(), "已有观众行要记下待删时刻"

        # 未满 24 小时、也没有下播：此刻不该删；定时器已在等（等待函数被调用，尚未到点）
        assert await privacy.execute_due_forget_deletions() == 0
        assert await store.viewers.get_viewer(viewer_id=viewer_id) is not None
        assert started_calls == [GRACE_DELETE_MAX_MS]

        # 时间过去 24 小时后放行兜底定时器；不真实等待
        clock.tick(GRACE_DELETE_MAX_MS)
        release_timer.set()
        await privacy.wait_grace_timers()
        assert started_calls == [GRACE_DELETE_MAX_MS]
        assert await store.viewers.get_viewer(viewer_id=viewer_id) is None
    finally:
        await privacy.close()
        await store.close()
