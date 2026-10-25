"""v17 → v18：观众表改为一行一人，外部 ID 分列记录。

1. **viewers 重建**：原来一行 = ``(platform, user_id)``，``user_id`` 可能是
   B 站账号 uid，也可能是开放平台 open_id，看不出种类。按 ID 形式拆进
   ``account_id``（纯数字）或 ``open_id``（UUID 形式；``open_app_id`` 留空，
   之后第一次被同一个 open_id 命中时补上当前应用）。旧版采集器漏写平台
   （``platform`` 为空）的行：纯数字 ID 归入 B 站账号、与已有同 uid 的行
   合并；``bili_`` 加昵称的兜底身份没有真实 ID，保留为两种 ID 都为空的
   B 站观众。``name_seen_ms`` 取原 ``last_active_ms``；明细全部是模拟数据的
   观众标为模拟观众。
2. **明细四表**加 ``viewer_id`` / ``id_kind`` 并回填，原始外部 ID 列保留。
3. **viewer_profiles** 改为以 ``viewer_id`` 为唯一键；对不上观众的画像丢弃，
   两份画像落到同一观众时保留较新的一份。
4. **viewer_names** 从现存明细按观众汇总用过的昵称回填，再补上当前昵称。

新建库的 DDL 已是最新形态；回调以列存在性检查保证对新库与重复执行安全。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Dict, List, Optional, Tuple

from src.modules.storage.migrations._common import column_exists, table_exists
from src.modules.storage.repos.viewers import is_placeholder_name
from src.modules.storage.schema import _VIEWER_PROFILES_SQL, _VIEWERS_SQL

_BILIBILI = "bilibili"
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# 旧版采集器缺 uid 时用"bili_昵称"顶替身份，这类 ID 不是真实账号
_LEGACY_NICKNAME_PREFIX = "bili_"
_DETAIL_TABLES: Tuple[Tuple[str, str, str], ...] = (
    # (表名, 外部 ID 列, 只回填观众行的附加条件)
    ("live_chat", "sender_id", " AND sender_role='viewer'"),
    ("gifts", "user_id", ""),
    ("super_chats", "user_id", ""),
    ("guards", "user_id", ""),
)
_COUNT_COLUMNS = (
    "message_count",
    "gift_count",
    "replied_count",
    "interaction_count",
    "paid_count",
    "paid_amount",
)


def _classify(platform: str, external_id: str) -> Tuple[str, str]:
    """按旧行的平台与 ID 形式判断归属：返回 ``(新平台, ID 种类)``，种类为空表示没有真实 ID。

    纯数字是 B 站账号 uid，UUID 形式是开放平台 open_id；旧版采集器漏写平台的行
    补成 B 站，其中"bili_昵称"兜底身份没有真实 ID。其余平台与非标准 ID（模拟器、
    调试注入）按账号 ID 保留，仍可按原 ID 找到。
    """
    value = external_id or ""
    is_digits = value.isascii() and value.isdigit()
    if not platform and (is_digits or value.startswith(_LEGACY_NICKNAME_PREFIX)):
        return _BILIBILI, ("account" if is_digits else "")
    if platform == _BILIBILI and _UUID_RE.match(value):
        return platform, "open"
    return platform, "account"


def _simulated_identities(conn: sqlite3.Connection) -> Dict[Tuple[str, str], bool]:
    """``(platform, 外部 ID)`` → 是否全部明细都是模拟数据（没有明细的观众不在表内）。"""
    flags: Dict[Tuple[str, str], bool] = {}
    for table, id_column, extra in _DETAIL_TABLES:
        if not table_exists(conn, table) or not column_exists(conn, table, "simulated"):
            continue
        rows = conn.execute(
            f"SELECT platform, {id_column} AS uid, MIN(simulated) AS all_sim FROM {table}"  # noqa: S608 常量表名
            f" WHERE {id_column} IS NOT NULL{extra} GROUP BY platform, {id_column}"
        ).fetchall()
        for row in rows:
            key = (str(row[0] or ""), str(row[1]))
            flags[key] = flags.get(key, True) and bool(row[2])
    return flags


def _rebuild_viewers(conn: sqlite3.Connection) -> None:
    """重建 viewers，并建临时映射表 ``_v18_viewer_map(旧 platform, 旧 user_id → 新观众 ID)``。"""
    simulated = _simulated_identities(conn)
    old_rows = conn.execute(
        "SELECT * FROM viewers ORDER BY (platform = '') ASC, last_active_ms DESC, id ASC"
    ).fetchall()
    merged: Dict[Tuple[str, str, str], Dict[str, object]] = {}
    mapping: List[Tuple[str, str, Tuple[str, str, str]]] = []
    for row in old_rows:
        old_platform, old_uid = str(row["platform"] or ""), str(row["user_id"])
        platform, kind = _classify(old_platform, old_uid)
        # 没有真实 ID 的兜底身份各自成行，不参与合并
        key = (platform, kind, old_uid) if kind else (platform, "", f"#{row['id']}")
        mapping.append((old_platform, old_uid, key))
        is_sim = simulated.get((old_platform, old_uid), False)
        target = merged.get(key)
        if target is None:
            merged[key] = {
                "id": int(row["id"]),
                "user_name": str(row["user_name"]),
                "last_active_ms": int(row["last_active_ms"]),
                "simulated": is_sim,
                **{col: int(row[col] or 0) for col in _COUNT_COLUMNS},
            }
            continue
        # 同一 uid 既有带平台的行又有漏写平台的行：计数相加，最近活跃取较晚的一方
        for col in _COUNT_COLUMNS:
            target[col] = int(target[col]) + int(row[col] or 0)
        if int(row["last_active_ms"]) > int(target["last_active_ms"]):
            target["last_active_ms"] = int(row["last_active_ms"])
            target["user_name"] = str(row["user_name"])
        target["simulated"] = bool(target["simulated"]) and is_sim

    conn.execute(_VIEWERS_SQL.replace("CREATE TABLE IF NOT EXISTS viewers (", "CREATE TABLE viewers_v18_new ("))
    for (platform, kind, value), data in merged.items():
        conn.execute(
            "INSERT INTO viewers_v18_new (id, platform, account_id, open_id, open_app_id, user_name, name_seen_ms,"
            " simulated, message_count, gift_count, replied_count, interaction_count, paid_count, paid_amount,"
            " last_active_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["id"],
                platform,
                value if kind == "account" else None,
                value if kind == "open" else None,
                "" if kind == "open" else None,
                data["user_name"],
                data["last_active_ms"],
                1 if data["simulated"] else 0,
                *(data[col] for col in _COUNT_COLUMNS),
                data["last_active_ms"],
            ),
        )
    conn.execute("DROP TABLE viewers")
    conn.execute("ALTER TABLE viewers_v18_new RENAME TO viewers")

    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _v18_viewer_map ("
        "platform TEXT NOT NULL, user_id TEXT NOT NULL, viewer_id INTEGER NOT NULL, PRIMARY KEY(platform, user_id))"
    )
    for old_platform, old_uid, key in mapping:
        conn.execute(
            "INSERT OR REPLACE INTO _v18_viewer_map(platform, user_id, viewer_id) VALUES (?, ?, ?)",
            (old_platform, old_uid, merged[key]["id"]),
        )


def _id_kind(platform: Optional[str], external_id: Optional[str]) -> str:
    """明细行 ``id_kind`` 回填用：与观众行同一套判断。"""
    return _classify(str(platform or ""), str(external_id or ""))[1]


def _backfill_details(conn: sqlite3.Connection, *, has_map: bool) -> None:
    """明细四表补 ``viewer_id`` / ``id_kind`` 列；有映射表时按旧身份回填。"""
    conn.create_function("v18_id_kind", 2, _id_kind, deterministic=True)
    for table, id_column, extra in _DETAIL_TABLES:
        if not table_exists(conn, table):
            continue
        if not column_exists(conn, table, "viewer_id"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN viewer_id INTEGER")  # noqa: S608 常量表名
        if not column_exists(conn, table, "id_kind"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN id_kind TEXT NOT NULL DEFAULT ''")  # noqa: S608
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_viewer ON {table}(viewer_id)")  # noqa: S608
        if not has_map:
            continue
        conn.execute(
            f"UPDATE {table} SET id_kind = v18_id_kind(platform, {id_column}),"  # noqa: S608 常量表名/列名
            " viewer_id = (SELECT m.viewer_id FROM _v18_viewer_map m"
            f" WHERE m.platform = {table}.platform AND m.user_id = {table}.{id_column})"
            f" WHERE viewer_id IS NULL AND {id_column} IS NOT NULL{extra}"
        )


def _rebuild_profiles(conn: sqlite3.Connection) -> None:
    """viewer_profiles 改以 ``viewer_id`` 为键；同一观众多份画像时保留较新的一份。"""
    if not table_exists(conn, "viewer_profiles") or not column_exists(conn, "viewer_profiles", "user_id"):
        return
    conn.execute(
        _VIEWER_PROFILES_SQL.replace(
            "CREATE TABLE IF NOT EXISTS viewer_profiles (", "CREATE TABLE viewer_profiles_v18_new ("
        )
    )
    conn.execute(
        "INSERT OR IGNORE INTO viewer_profiles_v18_new (viewer_id, profile_text, last_compressed_at_ms, updated_at_ms)"
        " SELECT m.viewer_id, p.profile_text, p.last_compressed_at_ms, p.updated_at_ms"
        " FROM viewer_profiles p JOIN _v18_viewer_map m ON m.platform = p.platform AND m.user_id = p.user_id"
        " ORDER BY p.updated_at_ms DESC"
    )
    conn.execute("DROP TABLE viewer_profiles")
    conn.execute("ALTER TABLE viewer_profiles_v18_new RENAME TO viewer_profiles")


def _backfill_names(conn: sqlite3.Connection) -> None:
    """viewer_names：现存明细里每位观众用过的昵称（最早/最晚出现时刻），再补上当前昵称。"""
    conn.create_function("v18_is_placeholder", 1, lambda name: 1 if is_placeholder_name(name) else 0)
    conn.execute(
        "INSERT OR IGNORE INTO viewer_names (viewer_id, user_name, first_seen_ms, last_seen_ms)"
        " SELECT viewer_id, name, MIN(ts), MAX(ts) FROM ("
        "  SELECT viewer_id, sender_name AS name, timestamp_ms AS ts FROM live_chat"
        "   WHERE sender_role='viewer' AND viewer_id IS NOT NULL AND sender_name IS NOT NULL"
        "  UNION ALL SELECT viewer_id, user_name, timestamp_ms FROM gifts WHERE viewer_id IS NOT NULL"
        "  UNION ALL SELECT viewer_id, user_name, timestamp_ms FROM super_chats WHERE viewer_id IS NOT NULL"
        "  UNION ALL SELECT viewer_id, user_name, timestamp_ms FROM guards WHERE viewer_id IS NOT NULL"
        " ) WHERE v18_is_placeholder(name) = 0 GROUP BY viewer_id, name"
    )
    conn.execute(
        "INSERT OR IGNORE INTO viewer_names (viewer_id, user_name, first_seen_ms, last_seen_ms)"
        " SELECT id, user_name, name_seen_ms, name_seen_ms FROM viewers WHERE v18_is_placeholder(user_name) = 0"
    )


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：viewers 重建 + 明细补列回填 + 画像改键 + 昵称记录回填 + 索引。"""
    rebuilt = table_exists(conn, "viewers") and not column_exists(conn, "viewers", "account_id")
    if rebuilt:
        _rebuild_viewers(conn)
    _backfill_details(conn, has_map=rebuilt)
    if rebuilt:
        _rebuild_profiles(conn)
        _backfill_names(conn)
        conn.execute("DROP TABLE IF EXISTS _v18_viewer_map")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_viewers_name ON viewers(platform, user_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_viewer_names_name ON viewer_names(user_name)")


__all__ = ["migrate"]
