"""
SQLiteStore 单元测试（Wave 3 / §1.50 / §1.53 9b）

覆盖：
- 11 张权威表 + schema_migrations 全部建出
- schema_migrations 记录当前版本
- ``simulated`` 列存在且默认 0（§1.6 贯穿列）
- 消费者 WHERE ``simulated=0`` 排除模拟数据（viewers / topics 统计视图中模拟数据零污染）
- is_healthy / list_tables / assert_schema_ready

权威参考：.omo/drafts/amaidesu-v2-storage-schema.md
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.storage import (
    SCHEMA_VERSION,
    SQLiteConnectionManager,
    SQLiteStore,
    list_expected_tables,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    """提供临时 DB 路径，测试结束清理。"""
    td = Path(tempfile.mkdtemp(prefix="w3-storage-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


# =============================================================================
# 11 张权威表 + schema_migrations
# =============================================================================


@pytest.mark.asyncio
async def test_eleven_tables_created(store: SQLiteStore) -> None:
    """11 张权威表 + schema_migrations 全部应被建立。"""
    for table_name in list_expected_tables():
        exists = await store.table_exists(table_name)
        assert exists, f"缺少权威表: {table_name}"
    # 总数 = 11 业务表 + 1 schema_migrations = 12
    tables = await store.list_tables()
    expected_count = len(list_expected_tables())
    assert len(tables) >= expected_count, f"表数 {len(tables)} < 期望 {expected_count}"
    # 业务表 11 张都在
    business_tables = [t for t in list_expected_tables() if t != "schema_migrations"]
    for t in business_tables:
        assert t in tables, f"业务表 {t} 不在 sqlite_master"


@pytest.mark.asyncio
async def test_schema_migration_recorded(store: SQLiteStore) -> None:
    """schema_migrations 记录当前 SCHEMA_VERSION。"""
    version = await store.get_schema_version()
    assert version == SCHEMA_VERSION, f"已应用版本={version} 应等于代码期望={SCHEMA_VERSION}"


@pytest.mark.asyncio
async def test_assert_schema_ready_passes(store: SQLiteStore) -> None:
    """assert_schema_ready 自检通过。"""
    await store.assert_schema_ready()


# =============================================================================
# simulated 贯穿列（§1.6 用户拍板：默认 False / True 标记 / 消费方排除）
# =============================================================================


@pytest.mark.asyncio
async def test_simulated_column_present_on_live_chat(store: SQLiteStore) -> None:
    """live_chat 表必须包含 ``simulated`` 列（default 0）。"""
    rows = await store.execute("PRAGMA table_info(live_chat)")
    cols = {row["name"]: row for row in rows}
    assert "simulated" in cols, "live_chat 缺少 simulated 列（§1.6 贯穿列缺失）"
    # 默认 0 = 真实源
    assert int(cols["simulated"]["dflt_value"]) == 0, "simulated 列默认值应为 0（真实源）"


@pytest.mark.asyncio
async def test_simulated_column_present_on_gifts(store: SQLiteStore) -> None:
    """gifts 表必须包含 simulated 列。"""
    rows = await store.execute("PRAGMA table_info(gifts)")
    cols = {row["name"]: row for row in rows}
    assert "simulated" in cols


@pytest.mark.asyncio
async def test_simulated_column_present_on_super_chats(store: SQLiteStore) -> None:
    """super_chats 表必须包含 simulated 列。"""
    rows = await store.execute("PRAGMA table_info(super_chats)")
    cols = {row["name"]: row for row in rows}
    assert "simulated" in cols


@pytest.mark.asyncio
async def test_simulated_exclusion_query(store: SQLiteStore) -> None:
    """消费者 WHERE ``simulated=0`` 排除模拟数据——模拟观众不污染真实统计。

    模拟场景：
    - 插入 1 条真实礼物（simulated=False）+ 1 条模拟礼物（simulated=True）
    - 查询 WHERE ``simulated=0`` 应只返回 1 条真实礼物
    - 同演示：viewers 统计派生自此排除
    """
    # 礼物明细
    await store.execute(
        "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type) "
        "VALUES (1, 1700000000000, 'viewer', '真实弹幕', 'danmaku')"
    )
    await store.execute(
        "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type, simulated) "
        "VALUES (1, 1700000001000, 'viewer', '模拟弹幕', 'danmaku', 1)"
    )

    real_rows = await store.execute("SELECT * FROM live_chat WHERE simulated=0")
    assert len(real_rows) == 1, f"消费者查询应返回 1 条真实数据，实得 {len(real_rows)}"
    assert str(real_rows[0]["content"]) == "真实弹幕"

    # 全部
    all_rows = await store.execute("SELECT * FROM live_chat")
    assert len(all_rows) == 2


# =============================================================================
# 健康检查 / 时间戳 / 字段命名规范（_ms）
# =============================================================================


@pytest.mark.asyncio
async def test_is_healthy(store: SQLiteStore) -> None:
    assert await store.is_healthy() is True


@pytest.mark.asyncio
async def test_millisecond_fields_exist(store: SQLiteStore) -> None:
    """权威表必须以 *_ms 为时间字段命名（§1.44）。抽查关键表。"""
    # live_sessions 必须有 started_at_ms / updated_at_ms
    rows = await store.execute("PRAGMA table_info(live_sessions)")
    cols = {row["name"] for row in rows}
    assert "started_at_ms" in cols
    assert "updated_at_ms" in cols
    assert "ended_at_ms" in cols
    # gifts
    rows = await store.execute("PRAGMA table_info(gifts)")
    cols_gifts = {row["name"] for row in rows}
    assert "timestamp_ms" in cols_gifts
    # topics
    rows = await store.execute("PRAGMA table_info(topics)")
    cols_topics = {row["name"] for row in rows}
    assert "duration_ms" in cols_topics


@pytest.mark.asyncio
async def test_message_type_values_live_chat(store: SQLiteStore) -> None:
    """message_type 列存在（live_chat 字段语义）。"""
    rows = await store.execute("PRAGMA table_info(live_chat)")
    cols = {row["name"] for row in rows}
    assert "message_type" in cols


# =============================================================================
# ConnectionManager（被 SQLiteStore 间接覆盖；此为直接单元测试）
# =============================================================================


@pytest.mark.asyncio
async def test_connection_manager_pragmas(temp_db_path: Path) -> None:
    """SQLiteConnectionManager 应设置 WAL + foreign_keys PRAGMA。"""
    manager = SQLiteConnectionManager(temp_db_path)
    conn = manager.connection()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()
        # WAL 模式；fetchone 返回 (mode,) 或者 sqlite3.Row
        mode_str = str(mode[0]) if not isinstance(mode, dict) else str(mode["journal_mode"])
        # WAL 模式可能以 "wal" 返回（部分连接)
        assert "wal" in mode_str.lower(), f"期望 WAL 模式，实得 {mode_str}"
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        fk_val = int(fk[0] if not isinstance(fk, dict) else fk["foreign_keys"])
        assert fk_val == 1, f"foreign_keys 应为 1，实得 {fk_val}"
    finally:
        manager.close_all()


@pytest.mark.asyncio
async def test_initialize_idempotent(temp_db_path: Path) -> None:
    """多次调用 initialize 不应报错（幂等）。"""
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    await s.initialize()
    await s.close()


# =============================================================================
# 领域查询方法：live_chat / viewers
# =============================================================================


async def _seed_chat(
    store: SQLiteStore,
    *,
    live_session_id: int,
    items: list[tuple[int, str, str, str]],
) -> None:
    """塞一批 ``(timestamp_ms, sender_id, sender_name, content)`` 弹幕。"""
    for ts, sid, sname, content in items:
        await store.insert_live_chat(
            live_session_id=live_session_id,
            timestamp_ms=ts,
            sender_role="viewer",
            sender_id=sid,
            sender_name=sname,
            content=content,
            message_type="danmaku",
        )


@pytest.mark.asyncio
async def test_list_recent_live_chat_returns_chronological(store: SQLiteStore) -> None:
    """回灌正序语义：5 条乱序时间戳，limit=3 返回**时间正序**的最近 3 条。

    关键不变量：先取最新窗口（DESC LIMIT 3），再 Python 内反转；
    不能直接 ASC LIMIT 3（会取到最老的 3 条）。
    """
    # 故意打乱插入顺序（模拟真实时序——消息不按入库顺序到达）
    items = [
        (3000, "u1", "张三", "第 3 条"),
        (1000, "u1", "张三", "第 1 条"),
        (5000, "u2", "李四", "第 5 条"),
        (2000, "u1", "张三", "第 2 条"),
        (4000, "u2", "李四", "第 4 条"),
    ]
    await _seed_chat(store, live_session_id=1, items=items)

    rows = await store.list_recent_live_chat(live_session_id=1, limit=3)
    assert len(rows) == 3, f"期望 3 条，实得 {len(rows)}"

    timestamps = [int(r["timestamp_ms"]) for r in rows]
    assert timestamps == [3000, 4000, 5000], (
        f"回灌语义错误：期望时间正序 [3000, 4000, 5000]，实得 {timestamps}"
    )

    contents = [str(r["content"]) for r in rows]
    assert contents == ["第 3 条", "第 4 条", "第 5 条"]


@pytest.mark.asyncio
async def test_list_recent_live_chat_before_window(store: SQLiteStore) -> None:
    """``before_timestamp_ms`` 窗口截断：只取 < 该阈值的消息，再按时间正序。"""
    items = [
        (1000, "u1", "张三", "msg-1"),
        (2000, "u1", "张三", "msg-2"),
        (3000, "u1", "张三", "msg-3"),
        (4000, "u1", "张三", "msg-4"),
        (5000, "u1", "张三", "msg-5"),
    ]
    await _seed_chat(store, live_session_id=1, items=items)

    # 窗口：before=4000 → 只看到 [1000, 2000, 3000]
    rows = await store.list_recent_live_chat(live_session_id=1, limit=10, before_timestamp_ms=4000)
    timestamps = [int(r["timestamp_ms"]) for r in rows]
    assert timestamps == [1000, 2000, 3000], f"窗口截断错误，实得 {timestamps}"

    # 窗口 + 限制：before=5000, limit=2 → 取 [3000, 4000]（4000 已被截断）
    rows2 = await store.list_recent_live_chat(
        live_session_id=1, limit=2, before_timestamp_ms=5000
    )
    timestamps2 = [int(r["timestamp_ms"]) for r in rows2]
    assert timestamps2 == [3000, 4000], f"窗口+limit 错误，实得 {timestamps2}"


@pytest.mark.asyncio
async def test_list_recent_live_chat_session_isolation(store: SQLiteStore) -> None:
    """不同 live_session_id 的消息互不串扰。"""
    await _seed_chat(store, live_session_id=1, items=[(1000, "u1", "张三", "s1-msg1")])
    await _seed_chat(store, live_session_id=2, items=[(2000, "u2", "李四", "s2-msg1")])

    rows1 = await store.list_recent_live_chat(live_session_id=1)
    rows2 = await store.list_recent_live_chat(live_session_id=2)
    assert len(rows1) == 1
    assert len(rows2) == 1
    assert int(rows1[0]["live_session_id"]) == 1
    assert int(rows2[0]["live_session_id"]) == 2


@pytest.mark.asyncio
async def test_list_messages_by_user_filters_and_window(store: SQLiteStore) -> None:
    """按 sender_id 过滤 + since_timestamp_ms 窗口 + 时间正序。"""
    items = [
        (1000, "u1", "张三", "u1-1"),
        (2000, "u2", "李四", "u2-1"),
        (3000, "u1", "张三", "u1-2"),
        (4000, "u1", "张三", "u1-3"),
        (5000, "u2", "李四", "u2-2"),
    ]
    await _seed_chat(store, live_session_id=1, items=items)

    # u1 全量（since=0 不过滤）→ 时间正序 [1000, 3000, 4000]
    rows = await store.list_messages_by_user(user_id="u1", since_timestamp_ms=0)
    timestamps = [int(r["timestamp_ms"]) for r in rows]
    assert timestamps == [1000, 3000, 4000], f"u1 全量错误，实得 {timestamps}"

    # u1 + 窗口 since=2000
    rows2 = await store.list_messages_by_user(user_id="u1", since_timestamp_ms=2000)
    timestamps2 = [int(r["timestamp_ms"]) for r in rows2]
    assert timestamps2 == [3000, 4000], f"u1 窗口错误，实得 {timestamps2}"

    # u2 全量
    rows_u2 = await store.list_messages_by_user(user_id="u2", since_timestamp_ms=0)
    assert [int(r["timestamp_ms"]) for r in rows_u2] == [2000, 5000]

    # 不存在的用户
    rows_none = await store.list_messages_by_user(user_id="nobody", since_timestamp_ms=0)
    assert rows_none == []


@pytest.mark.asyncio
async def test_upsert_viewer_message_count(store: SQLiteStore) -> None:
    """upsert_viewer_message：首次插入 + 再次 upsert 后 message_count=2。"""
    ts1 = 1_700_000_000_000
    ts2 = 1_700_000_000_500

    # 首次
    await store.upsert_viewer_message(user_id="u1", user_name="张三", timestamp_ms=ts1)
    row = await store.get_viewer_stats(user_id="u1")
    assert row is not None
    assert int(row["message_count"]) == 1
    assert int(row["gift_count"]) == 0
    assert int(row["replied_count"]) == 0
    assert int(row["interaction_count"]) == 1
    assert int(row["last_active_ms"]) == ts1
    assert str(row["user_name"]) == "张三"

    # 再次
    await store.upsert_viewer_message(user_id="u1", user_name="张三（新昵称）", timestamp_ms=ts2)
    row2 = await store.get_viewer_stats(user_id="u1")
    assert row2 is not None
    assert int(row2["message_count"]) == 2, "message_count 应累计到 2"
    assert int(row2["interaction_count"]) == 2, "interaction_count 应累计到 2"
    assert int(row2["last_active_ms"]) == ts2, "last_active_ms 应更新"
    assert str(row2["user_name"]) == "张三（新昵称）", "user_name 应被 excluded 覆盖"


@pytest.mark.asyncio
async def test_upsert_viewer_gift_count(store: SQLiteStore) -> None:
    """upsert_viewer_gift：gift_count 与 interaction_count 各 +1；不影响 message_count。"""
    ts1 = 1_700_000_001_000
    ts2 = 1_700_000_002_000

    # 先 1 条 message，再 1 次 gift
    await store.upsert_viewer_message(user_id="u1", user_name="张三", timestamp_ms=ts1)
    await store.upsert_viewer_gift(user_id="u1", user_name="张三", timestamp_ms=ts2)

    row = await store.get_viewer_stats(user_id="u1")
    assert row is not None
    assert int(row["message_count"]) == 1, "gift upsert 不应改 message_count"
    assert int(row["gift_count"]) == 1, "gift_count 应为 1"
    assert int(row["interaction_count"]) == 2, "interaction_count 应累计 1+1=2"
    assert int(row["last_active_ms"]) == ts2


@pytest.mark.asyncio
async def test_upsert_viewer_replied_count(store: SQLiteStore) -> None:
    """upsert_viewer_replied：+1 replied，+1 interaction；未存在的 user_id 首建 user_name="?"。"""
    # 未存在的用户：首建
    await store.upsert_viewer_replied(user_id="u_new", timestamp_ms=1_700_000_010_000)
    row = await store.get_viewer_stats(user_id="u_new")
    assert row is not None
    assert int(row["replied_count"]) == 1
    assert int(row["interaction_count"]) == 1
    assert int(row["message_count"]) == 0
    assert int(row["gift_count"]) == 0
    assert str(row["user_name"]) == "?", "首建 user_name 应占位 '?'"

    # 再次 replied
    await store.upsert_viewer_replied(user_id="u_new", timestamp_ms=1_700_000_010_500)
    row2 = await store.get_viewer_stats(user_id="u_new")
    assert row2 is not None
    assert int(row2["replied_count"]) == 2
    assert int(row2["interaction_count"]) == 2


@pytest.mark.asyncio
async def test_upsert_viewer_replied_keeps_known_name(store: SQLiteStore) -> None:
    """upsert_viewer_replied 不应覆盖已知 user_name（DO UPDATE 不动 user_name）。"""
    # 先 message upsert 写 user_name，再 replied upsert
    await store.upsert_viewer_message(user_id="u1", user_name="张三", timestamp_ms=1_700_000_000_000)
    await store.upsert_viewer_replied(user_id="u1", timestamp_ms=1_700_000_001_000)
    row = await store.get_viewer_stats(user_id="u1")
    assert row is not None
    assert str(row["user_name"]) == "张三", "replied upsert 不应覆盖已有 user_name"
    assert int(row["replied_count"]) == 1
    assert int(row["message_count"]) == 1


@pytest.mark.asyncio
async def test_get_viewer_stats_hit_and_miss(store: SQLiteStore) -> None:
    """get_viewer_stats：命中返回 Row，未命中返回 None。"""
    # 命中
    await store.upsert_viewer_message(user_id="u1", user_name="张三", timestamp_ms=1_700_000_000_000)
    hit = await store.get_viewer_stats(user_id="u1")
    assert hit is not None
    assert str(hit["user_id"]) == "u1"

    # 未命中
    miss = await store.get_viewer_stats(user_id="nobody")
    assert miss is None


@pytest.mark.asyncio
async def test_list_viewer_stats_order_by_whitelist(store: SQLiteStore) -> None:
    """list_viewer_stats：合法 order_by 排序、limit 生效；非法值抛 ValueError。"""
    # 准备 3 个不同指标的观众
    await store.upsert_viewer_message(
        user_id="u_msg", user_name="msg 用户", timestamp_ms=1_700_000_000_000
    )
    await store.upsert_viewer_message(
        user_id="u_msg", user_name="msg 用户", timestamp_ms=1_700_000_001_000
    )
    await store.upsert_viewer_gift(
        user_id="u_gift", user_name="gift 用户", timestamp_ms=1_700_000_002_000
    )
    await store.upsert_viewer_gift(
        user_id="u_gift", user_name="gift 用户", timestamp_ms=1_700_000_003_000
    )
    await store.upsert_viewer_gift(
        user_id="u_gift", user_name="gift 用户", timestamp_ms=1_700_000_004_000
    )
    await store.upsert_viewer_replied(user_id="u_repl", timestamp_ms=1_700_000_005_000)
    await store.upsert_viewer_replied(user_id="u_repl", timestamp_ms=1_700_000_006_000)

    # 默认 order_by=message_count：u_msg 排第一
    rows = await store.list_viewer_stats()
    assert len(rows) == 3
    assert int(rows[0]["message_count"]) == 2
    assert str(rows[0]["user_id"]) == "u_msg"

    # 切到 gift_count
    rows_gift = await store.list_viewer_stats(order_by="gift_count")
    assert str(rows_gift[0]["user_id"]) == "u_gift"
    assert int(rows_gift[0]["gift_count"]) == 3

    # 切到 last_active_ms
    rows_active = await store.list_viewer_stats(order_by="last_active_ms")
    assert str(rows_active[0]["user_id"]) == "u_repl"

    # 切到 replied_count
    rows_repl = await store.list_viewer_stats(order_by="replied_count")
    assert str(rows_repl[0]["user_id"]) == "u_repl"
    assert int(rows_repl[0]["replied_count"]) == 2

    # limit 生效
    rows_limited = await store.list_viewer_stats(limit=2)
    assert len(rows_limited) == 2

    # 非法 order_by → ValueError
    with pytest.raises(ValueError):
        await store.list_viewer_stats(order_by="user_name")  # noqa: S106 白名单外
    with pytest.raises(ValueError):
        await store.list_viewer_stats(order_by="1; DROP TABLE viewers--")  # noqa: S106 注入尝试
    with pytest.raises(ValueError):
        await store.list_viewer_stats(order_by="")  # noqa: S106 空字符串
