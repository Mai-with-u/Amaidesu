"""观众身份的人工处理：待确认列表、保持新建、合并（ID / 计数 / 昵称 / 明细 / 画像 / 并入建议）、取消关联（明细改指与计数重算）。"""

from __future__ import annotations

from typing import Any

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos.viewer_identity import ViewerIdentityError

DAY = 86_400_000
WINDOW = 90 * DAY
OPEN_ID = "39b8fedb-60a5-4e29-ac75-b16955f7e632"


async def _says(
    store: SQLiteDatabase, external_id: str, name: str, ts: int, *, kind: str = "account", **kw: Any
) -> int:
    """模拟记账器收到一条弹幕：解析观众 → 落带观众 ID 的明细 → 计数，返回观众 ID。"""
    result = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind=kind,  # type: ignore[arg-type]
        external_id=external_id,
        user_name=name,
        timestamp_ms=ts,
        name_match_window_ms=WINDOW,
        open_app_id="app1" if kind == "open" else "",
    )
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=ts,
        platform="bilibili",
        sender_role="viewer",
        sender_id=external_id,
        sender_name=name,
        content="话",
        message_type="danmaku",
        message_id=kw.get("message_id"),
        viewer_id=result.viewer_id,
        id_kind=kind,
    )
    await store.viewers.record_message(viewer_id=result.viewer_id, timestamp_ms=ts)
    return result.viewer_id


async def _row(store: SQLiteDatabase, viewer_id: int) -> Any:
    rows = await store.execute("SELECT * FROM viewers WHERE id=?", (viewer_id,))
    return rows[0] if rows else None


async def _pending_pair(store: SQLiteDatabase) -> tuple[int, int]:
    """老观众（账号 ID）+ 超出时限后同名出现的开放平台 ID → 待确认观众。返回 (老, 待确认)。"""
    old = await _says(store, "1001", "甲", 1_000)
    pending = await _says(store, OPEN_ID, "甲", 1_000 + WINDOW + 1, kind="open")
    assert old != pending
    return old, pending


async def test_list_pending_and_keep_separate(store: SQLiteDatabase) -> None:
    identity = store.viewer_identity
    old, pending = await _pending_pair(store)
    total, rows = await identity.list_pending()
    assert total == 1
    assert (rows[0].viewer_id, rows[0].candidate_id, rows[0].candidate_account_id) == (pending, old, "1001")

    assert await identity.keep_separate(viewer_id=pending) is True
    assert (await _row(store, pending))["link_status"] == "confirmed"
    assert await identity.keep_separate(viewer_id=pending) is False  # 已不在待确认
    assert (await identity.list_pending())[0] == 0


async def test_merge_pending_into_candidate(store: SQLiteDatabase) -> None:
    old, pending = await _pending_pair(store)
    await store.viewer_profiles.upsert_profile(viewer_id=pending, profile_text="新档画像", last_compressed_at_ms=1)
    await store.execute("UPDATE viewers SET replied_count=2 WHERE id=?", (pending,))

    await store.viewer_identity.merge(source_id=pending, target_id=old)

    assert await _row(store, pending) is None
    row = await _row(store, old)
    assert (row["account_id"], row["open_id"], row["open_app_id"]) == ("1001", OPEN_ID, "app1")
    assert (row["message_count"], row["replied_count"], row["name_seen_ms"]) == (2, 2, 1_000 + WINDOW + 1)
    chat = await store.execute("SELECT DISTINCT viewer_id FROM live_chat")
    assert [r["viewer_id"] for r in chat] == [old]  # 明细全部改指老观众
    # 老观众没有画像时沿用来源的
    assert await store.viewer_profiles.get_profile_text(viewer_id=old) == "新档画像"
    names = await store.execute("SELECT user_name FROM viewer_names WHERE viewer_id=?", (old,))
    assert [r["user_name"] for r in names] == ["甲"]
    assert (await store.viewer_identity.list_pending())[0] == 0


async def test_merge_rewires_other_suggestions_and_keeps_target_profile(store: SQLiteDatabase) -> None:
    a = await _says(store, "2001", "乙", 1_000)
    b = await _says(store, "2002", "丙", 2_000)
    # 第三位待确认观众建议并入 b；人工把 b 并入 a 之后，建议改指 a
    c = await _says(store, OPEN_ID, "丙", 2_000 + WINDOW + 1, kind="open")
    await store.viewer_profiles.upsert_profile(viewer_id=a, profile_text="A 的画像", last_compressed_at_ms=1)
    await store.viewer_profiles.upsert_profile(viewer_id=b, profile_text="B 的画像", last_compressed_at_ms=1)
    with pytest.raises(ViewerIdentityError, match="两个不同账号"):
        await store.viewer_identity.merge(source_id=b, target_id=a)  # 两个不同账号 ID 不能合并

    await store.execute("UPDATE viewers SET account_id=NULL WHERE id=?", (b,))  # 构造：b 没有账号 ID
    await store.viewer_identity.merge(source_id=b, target_id=a)
    assert (await _row(store, c))["link_candidate_id"] == a
    assert await store.viewer_profiles.get_profile_text(viewer_id=a) == "A 的画像"  # 目标画像为准
    assert await store.viewer_profiles.count_profiles() == 1
    # 昵称取最近出现的一方，两边昵称记录合并
    assert (await _row(store, a))["user_name"] == "丙"
    names = await store.execute("SELECT user_name FROM viewer_names WHERE viewer_id=? ORDER BY user_name", (a,))
    assert {r["user_name"] for r in names} == {"乙", "丙"}


@pytest.mark.parametrize(
    ("source_kwargs", "message"),
    [
        ({"platform": "console"}, "不同平台"),
        ({"simulated": 1}, "模拟观众"),
    ],
    ids=["cross-platform", "simulated"],
)
async def test_merge_refuses_mismatched_viewers(store: SQLiteDatabase, source_kwargs, message) -> None:
    a = await _says(store, "3001", "丁", 1_000)
    b = await _says(store, OPEN_ID, "戊", 2_000, kind="open")
    for column, value in source_kwargs.items():
        await store.execute(f"UPDATE viewers SET {column}=? WHERE id=?", (value, b))
    with pytest.raises(ViewerIdentityError, match=message):
        await store.viewer_identity.merge(source_id=b, target_id=a)
    with pytest.raises(ViewerIdentityError, match="自己"):
        await store.viewer_identity.merge(source_id=a, target_id=a)
    with pytest.raises(ViewerIdentityError, match="不存在"):
        await store.viewer_identity.merge(source_id=a, target_id=9999)


async def test_unlink_moves_details_and_recounts(store: SQLiteDatabase) -> None:
    # 自动关联错了：老观众"甲"（账号 ID）被关联上了一个拿了同名的开放平台 ID
    vid = await _says(store, "1001", "甲", 1_000, message_id="m1")
    assert await _says(store, OPEN_ID, "甲", 2_000, kind="open", message_id="m2") == vid
    await _says(store, OPEN_ID, "甲二号", 3_000, kind="open", message_id="m3")
    await store.chat.insert_live_chat(
        live_session_id=1, timestamp_ms=3_500, sender_role="assistant", content="回你", message_type="speak",
        reply_to_message_id="m3",
    )  # fmt: skip
    await store.viewers.record_replied(viewer_id=vid, timestamp_ms=3_500)
    await store.viewer_profiles.upsert_profile(viewer_id=vid, profile_text="老观众画像", last_compressed_at_ms=1)

    new_id = await store.viewer_identity.unlink(viewer_id=vid, id_kind="open")

    orig, new = await _row(store, vid), await _row(store, new_id)
    assert (orig["account_id"], orig["open_id"]) == ("1001", None)
    assert (new["account_id"], new["open_id"], new["open_app_id"]) == (None, OPEN_ID, "app1")
    # 新观众计数按改指的明细重算（两条弹幕 + 一次被回复），原观众减去同样的数
    assert (new["message_count"], new["replied_count"], new["interaction_count"]) == (2, 1, 3)
    assert (orig["message_count"], orig["replied_count"], orig["interaction_count"]) == (1, 0, 1)
    assert (new["user_name"], new["name_seen_ms"], new["last_active_ms"]) == ("甲二号", 3_000, 3_000)
    # 原观众的当前昵称"甲二号"只来自被拆走的明细：改回剩余明细里最近的昵称
    assert orig["user_name"] == "甲"
    moved = await store.execute("SELECT message_id FROM live_chat WHERE viewer_id=? ORDER BY id", (new_id,))
    assert [r["message_id"] for r in moved] == ["m2", "m3"]
    # 画像留在原观众身上，新观众从零开始
    assert await store.viewer_profiles.get_profile_text(viewer_id=vid) == "老观众画像"
    assert await store.viewer_profiles.get_profile_text(viewer_id=new_id) is None
    names = await store.execute("SELECT user_name FROM viewer_names WHERE viewer_id=? ORDER BY user_name", (vid,))
    assert [r["user_name"] for r in names] == ["甲"]


async def test_unlink_refuses_last_or_missing_id(store: SQLiteDatabase) -> None:
    vid = await _says(store, "1001", "甲", 1_000)
    with pytest.raises(ViewerIdentityError, match="没有开放平台 ID"):
        await store.viewer_identity.unlink(viewer_id=vid, id_kind="open")
    with pytest.raises(ViewerIdentityError, match="只剩这一个 ID"):
        await store.viewer_identity.unlink(viewer_id=vid, id_kind="account")
    with pytest.raises(ViewerIdentityError, match="不存在"):
        await store.viewer_identity.unlink(viewer_id=9999, id_kind="account")
