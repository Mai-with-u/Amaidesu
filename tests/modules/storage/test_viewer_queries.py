"""观众域 Repo 查询单测（ViewerRepo list 升级 + ChatRepo 用户维度查询）

覆盖：
- list_viewer_stats：搜索（user_id/user_name 命中、LIKE 元字符转义）、
  分页（limit/offset 与 total 全计数解耦）
- viewer_insight_buckets：活跃分桶互斥（today/week/month/older）与覆盖计数
- list_user_dialogue：观众消息与主播回复交织正序、游标批次翻页
- get_user_activity_bounds：三表综合时间边界
- list_user_gifts / list_user_super_chats / summarize_user_contributions
- list_user_sessions：按场次聚合与标题 join
- daily_danmaku_counts：按天聚合、只计 danmaku 类型
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.time_utils import now_ms


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="viewer-queries-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path):
    db = SQLiteDatabase(temp_db_path)
    await db.initialize()
    yield db
    await db.close()


async def _seed_viewer(
    store: SQLiteDatabase,
    user_id: str,
    user_name: str,
    *,
    messages: int = 1,
    gifts: int = 0,
    last_active_ms: int | None = None,
) -> None:
    """造一行观众统计（发言/送礼计数与最后活跃时间）。"""
    ts = last_active_ms if last_active_ms is not None else now_ms()
    for _ in range(messages):
        await store.viewers.upsert_viewer_message(user_id=user_id, user_name=user_name, timestamp_ms=ts)
    for _ in range(gifts):
        await store.viewers.upsert_viewer_gift(user_id=user_id, user_name=user_name, timestamp_ms=ts)


# ===== list_viewer_stats：搜索与分页 =====


@pytest.mark.asyncio
async def test_list_search_by_name_and_id(store: SQLiteDatabase) -> None:
    await _seed_viewer(store, "u1", "小明")
    await _seed_viewer(store, "u2", "小红")
    rows, total = await store.viewers.list_viewer_stats(search="小明")
    assert total == 1
    assert [str(r["user_id"]) for r in rows] == ["u1"]

    rows, total = await store.viewers.list_viewer_stats(search="u2")
    assert total == 1
    assert [str(r["user_id"]) for r in rows] == ["u2"]

    rows, total = await store.viewers.list_viewer_stats(search="不存在的名字")
    assert total == 0
    assert rows == []


@pytest.mark.asyncio
async def test_list_search_like_metachars_literal(store: SQLiteDatabase) -> None:
    """搜索词中的 % 与 _ 按字面匹配，不当通配符解释。"""
    await _seed_viewer(store, "u1", "小明")
    await _seed_viewer(store, "u_", "带下划线的名字")
    rows, total = await store.viewers.list_viewer_stats(search="u_")
    # 命中字面 "u_"，不把 _ 当单字符通配匹配 "u1"
    assert total == 1
    assert [str(r["user_id"]) for r in rows] == ["u_"]


@pytest.mark.asyncio
async def test_list_pagination_total_decoupled_from_limit(store: SQLiteDatabase) -> None:
    for i in range(5):
        await _seed_viewer(store, f"u{i}", f"观众{i}")
    rows, total = await store.viewers.list_viewer_stats(limit=2, offset=0)
    assert total == 5
    assert len(rows) == 2
    rows, total = await store.viewers.list_viewer_stats(limit=2, offset=4)
    assert total == 5
    assert len(rows) == 1
    # 空档 offset 不报错
    rows, total = await store.viewers.list_viewer_stats(limit=2, offset=100)
    assert rows == []
    assert total == 5


@pytest.mark.asyncio
async def test_list_search_combines_with_pagination(store: SQLiteDatabase) -> None:
    for i in range(4):
        await _seed_viewer(store, f"vip{i}", f"贵宾{i}")
    await _seed_viewer(store, "other", "路人")
    rows, total = await store.viewers.list_viewer_stats(search="vip", limit=2, offset=2)
    assert total == 4
    assert len(rows) == 2


# ===== viewer_insight_buckets：活跃分桶 =====


@pytest.mark.asyncio
async def test_insight_buckets_mutually_exclusive(store: SQLiteDatabase) -> None:
    now = now_ms()
    # 造四个观众分别落在四个桶（以 now 为锚错开数量级，避开本地时区零点临界）
    await _seed_viewer(store, "u_today", "今天", last_active_ms=now)
    await _seed_viewer(store, "u_week", "本周", last_active_ms=now - 3 * 86_400_000)
    await _seed_viewer(store, "u_month", "本月", last_active_ms=now - 20 * 86_400_000)
    await _seed_viewer(store, "u_older", "更早", last_active_ms=now - 60 * 86_400_000)
    # u_today 送过礼；u_week 从未被回复（messages>0 但 replied_count=0）
    await store.viewers.upsert_viewer_gift(user_id="u_today", user_name="今天", timestamp_ms=now)

    buckets = await store.viewers.viewer_insight_buckets()
    assert buckets["total"] == 4
    assert buckets["active_today"] == 1
    assert buckets["active_week"] == 1
    assert buckets["active_month"] == 1
    assert buckets["active_older"] == 1
    assert buckets["gift_viewers"] == 1
    # upsert_viewer_message 不动 replied_count，全部为 0
    assert buckets["never_replied"] == 4


@pytest.mark.asyncio
async def test_insight_buckets_empty_table(store: SQLiteDatabase) -> None:
    buckets = await store.viewers.viewer_insight_buckets()
    assert buckets == {
        "total": 0,
        "active_today": 0,
        "active_week": 0,
        "active_month": 0,
        "active_older": 0,
        "never_replied": 0,
        "gift_viewers": 0,
    }


# ===== list_user_dialogue：交织与游标 =====


async def _seed_dialogue(store: SQLiteDatabase, session_id: int) -> dict[str, int]:
    """造一个观众 3 条消息 + 主播回复第 2 条的对话集，返回 message_id→ts。"""
    base = 1_700_000_000_000
    ts = {"m1": base, "m2": base + 1_000, "m3": base + 2_000}
    for mid, t in ts.items():
        await store.chat.insert_live_chat(
            live_session_id=session_id,
            timestamp_ms=t,
            sender_role="viewer",
            sender_id="u_d",
            sender_name="对话观众",
            content=f"消息{mid}",
            message_type="danmaku",
            message_id=mid,
        )
    await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=base + 1_500,
        sender_role="assistant",
        content="主播回复了 m2",
        message_type="speech",
        reply_to_message_id="m2",
    )
    return ts


@pytest.mark.asyncio
async def test_dialogue_interleaves_reply_in_order(store: SQLiteDatabase) -> None:
    ts = await _seed_dialogue(store, session_id=1)
    rows = await store.chat.list_user_dialogue(user_id="u_d", limit=10)
    # 4 行正序：m1 < m2 < reply(m2) < m3
    seq = [(str(r["sender_role"]), int(r["timestamp_ms"])) for r in rows]
    assert [s[0] for s in seq] == ["viewer", "viewer", "assistant", "viewer"]
    assert [s[1] for s in seq] == [ts["m1"], ts["m2"], ts["m2"] + 500, ts["m3"]]


@pytest.mark.asyncio
async def test_dialogue_cursor_batches(store: SQLiteDatabase) -> None:
    ts = await _seed_dialogue(store, session_id=1)
    # 首批取最近 2 条观众消息（m2/m3），reply(m2) 交织进来
    batch1 = await store.chat.list_user_dialogue(user_id="u_d", limit=2)
    assert [str(r["message_id"]) for r in batch1 if str(r["sender_role"]) == "viewer"] == ["m2", "m3"]
    assert any(str(r["sender_role"]) == "assistant" for r in batch1)
    # 游标翻更早：只取 < m2 时刻 → 仅 m1，无回复交织
    batch2 = await store.chat.list_user_dialogue(user_id="u_d", limit=2, before_timestamp_ms=ts["m2"])
    assert [str(r["message_id"]) for r in batch2] == ["m1"]


@pytest.mark.asyncio
async def test_dialogue_other_users_replies_excluded(store: SQLiteDatabase) -> None:
    """只交织对本观众消息的回复——回复他人消息的 assistant 行不混入。"""
    base = 1_700_000_000_000
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=base,
        sender_role="viewer",
        sender_id="u_d",
        sender_name="对话观众",
        content="我的消息",
        message_type="danmaku",
        message_id="mine",
    )
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=base + 100,
        sender_role="viewer",
        sender_id="u_other",
        sender_name="别人",
        content="别人的消息",
        message_type="danmaku",
        message_id="others",
    )
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=base + 200,
        sender_role="assistant",
        content="回复了别人",
        message_type="speech",
        reply_to_message_id="others",
    )
    rows = await store.chat.list_user_dialogue(user_id="u_d", limit=10)
    assert len(rows) == 1
    assert str(rows[0]["message_id"]) == "mine"


# ===== get_user_activity_bounds =====


@pytest.mark.asyncio
async def test_activity_bounds_covers_three_tables(store: SQLiteDatabase) -> None:
    base = 1_700_000_000_000
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=base + 5_000,
        sender_role="viewer",
        sender_id="u_b",
        content="发言",
        message_type="danmaku",
    )
    await store.chat.insert_gift(
        live_session_id=1, timestamp_ms=base, user_id="u_b", user_name="礼官", gift_name="小心心", gift_count=1
    )
    await store.chat.insert_super_chat(
        live_session_id=1, timestamp_ms=base + 9_000, user_id="u_b", user_name="礼官", amount=30.0, message="SC"
    )
    bounds = await store.chat.get_user_activity_bounds(user_id="u_b")
    assert bounds == (base, base + 9_000)


@pytest.mark.asyncio
async def test_activity_bounds_no_details_returns_none(store: SQLiteDatabase) -> None:
    assert await store.chat.get_user_activity_bounds(user_id="ghost") is None


# ===== 贡献：明细与汇总 =====


@pytest.mark.asyncio
async def test_user_contributions_detail_and_summary(store: SQLiteDatabase) -> None:
    base = 1_700_000_000_000
    await store.chat.insert_gift(
        live_session_id=1, timestamp_ms=base, user_id="u_c", user_name="贡献者", gift_name="小心心", gift_count=2
    )
    await store.chat.insert_gift(
        live_session_id=1, timestamp_ms=base + 1_000, user_id="u_c", user_name="贡献者", gift_name="辣条", gift_count=3
    )
    await store.chat.insert_super_chat(
        live_session_id=2, timestamp_ms=base + 2_000, user_id="u_c", user_name="贡献者", amount=50.0, message="加油"
    )
    gifts = await store.chat.list_user_gifts(user_id="u_c")
    assert [int(r["gift_count"]) for r in gifts] == [3, 2]  # 时间倒序
    scs = await store.chat.list_user_super_chats(user_id="u_c")
    assert len(scs) == 1
    summary = await store.chat.summarize_user_contributions(user_id="u_c")
    assert summary["gift_total_count"] == 5
    assert summary["sc_total_amount"] == 50.0
    assert summary["sc_total_count"] == 1


@pytest.mark.asyncio
async def test_user_contributions_empty(store: SQLiteDatabase) -> None:
    summary = await store.chat.summarize_user_contributions(user_id="nobody")
    assert summary == {"gift_total_count": 0, "sc_total_amount": 0.0, "sc_total_count": 0}
    assert await store.chat.list_user_gifts(user_id="nobody") == []


# ===== list_user_sessions =====


@pytest.mark.asyncio
async def test_user_sessions_grouped_and_titled(store: SQLiteDatabase) -> None:
    base = 1_700_000_000_000
    # 两场次各发言一次；场次 202 有真实标题（join 取回），101 不存在（LEFT JOIN 容忍 NULL）
    pk = await store.sessions.insert_live_session(started_at_ms=base + 86_400_000, title="周六场")
    await store.chat.insert_live_chat(
        live_session_id=101,
        timestamp_ms=base,
        sender_role="viewer",
        sender_id="u_s",
        sender_name="常客",
        content="第一场",
        message_type="danmaku",
    )
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=base + 86_400_000,
        sender_role="viewer",
        sender_id="u_s",
        sender_name="常客",
        content="第二场",
        message_type="danmaku",
    )
    rows = await store.chat.list_user_sessions(user_id="u_s")
    assert len(rows) == 2
    # 最近参与在前
    assert int(rows[0]["live_session_id"]) == pk
    assert str(rows[0]["title"]) == "周六场"
    assert int(rows[0]["message_count"]) == 1
    assert rows[1]["title"] is None


# ===== daily_danmaku_counts =====


@pytest.mark.asyncio
async def test_daily_danmaku_counts_only_danmaku_type(store: SQLiteDatabase) -> None:
    now = now_ms()
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=now,
        sender_role="viewer",
        sender_id="u1",
        content="今天的弹幕1",
        message_type="danmaku",
    )
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=now - 1_000,
        sender_role="viewer",
        sender_id="u2",
        content="今天的弹幕2",
        message_type="danmaku",
    )
    # 非 danmaku 类型不计入
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=now,
        sender_role="viewer",
        sender_id="u3",
        content="上舰文本",
        message_type="guard",
    )
    rows = await store.chat.daily_danmaku_counts(days=7)
    assert len(rows) == 1
    assert int(rows[0]["count"]) == 2
    # 回看 0 天（仅今日）不报错
    rows_today = await store.chat.daily_danmaku_counts(days=1)
    assert int(rows_today[0]["count"]) == 2
