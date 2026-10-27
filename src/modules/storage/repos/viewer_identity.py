"""ViewerIdentityRepo —— 观众身份的人工处理：待确认列表、合并、保持新建、取消关联。

自动解析（``ViewerRepo.resolve_viewer``）只在"时限内唯一同名候选"时把新 ID 记到
老观众身上，其余同名情况先建新观众并标为待确认。这里承接 WebUI 上的人工处理：

- **合并**（一位观众并入另一位）：外部 ID 搬到目标观众；两边同一种 ID 都有值且
  不同（两个不同账号）时拒绝。计数相加，昵称取最近出现的一方，昵称记录合并，
  明细改指目标观众；画像以目标为准，目标没有画像时沿用来源的。
- **保持新建**：清除待确认标记，作为独立观众保留。
- **取消关联**：把某一个外部 ID 从观众身上拆出去，用它新建一位观众；明细里原始
  外部 ID 及种类相同的行改指新观众，新观众的计数按这些明细重算，原观众减去同样
  的数。已删除场次的明细拆不出来，那部分计数留在原观众身上；画像留在原观众身上。

合并与拆分都跨 viewers / viewer_names / viewer_profiles / 明细四表，在一个立即事务
里完成，避免与记账器的并发写交错。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Literal, Optional, Tuple

from src.modules.storage.models.viewer_identity import PendingViewerSummary
from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms

IdKind = Literal["account", "open"]

# (表名, 原始外部 ID 列, 昵称列, 只取观众行的附加条件)
_DETAIL_TABLES: Tuple[Tuple[str, str, str, str], ...] = (
    ("live_chat", "sender_id", "sender_name", " AND sender_role='viewer'"),
    ("gifts", "user_id", "user_name", ""),
    ("super_chats", "user_id", "user_name", ""),
    ("guards", "user_id", "user_name", ""),
)
_COUNT_COLUMNS = ("message_count", "gift_count", "replied_count", "interaction_count", "paid_count", "paid_amount")
# 不计付费的币种：银瓜子（免费礼物）与空币种（调试数据），与记账器口径一致
_UNPAID_CURRENCIES = ("bilibili_silver_coin", "")
_ID_LABELS = {"account_id": "账号 ID", "open_id": "开放平台 ID"}


class ViewerIdentityError(ValueError):
    """人工处理请求不成立（观众不存在、跨平台、两个不同账号等），消息可直接展示给用户。"""


class ViewerIdentityRepo(BaseRepo):
    """观众身份的人工处理（WebUI 待确认页与观众档案页的写操作）。"""

    async def list_pending(self, *, limit: int = 50, offset: int = 0) -> Tuple[int, List[PendingViewerSummary]]:
        """待确认观众（最近活跃在前）及其建议并入的老观众摘要，返回 ``(总数, 当页行)``。"""
        page_limit = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))

        def _exec() -> Tuple[int, List[PendingViewerSummary]]:
            with self._manager.transaction() as conn:
                total_row = conn.execute("SELECT COUNT(*) AS n FROM viewers WHERE link_status='pending'").fetchone()
                rows = conn.execute(
                    "SELECT v.id AS viewer_id, v.platform, v.user_name, v.account_id, v.open_id, v.open_app_id,"
                    " v.name_seen_ms, v.message_count, v.interaction_count, v.paid_amount, v.last_active_ms,"
                    " c.id AS candidate_id, c.user_name AS candidate_name, c.account_id AS candidate_account_id,"
                    " c.open_id AS candidate_open_id, c.name_seen_ms AS candidate_name_seen_ms,"
                    " c.message_count AS candidate_message_count, c.interaction_count AS candidate_interaction_count,"
                    " c.paid_amount AS candidate_paid_amount, c.last_active_ms AS candidate_last_active_ms"
                    " FROM viewers v LEFT JOIN viewers c ON c.id = v.link_candidate_id"
                    " WHERE v.link_status='pending' ORDER BY v.last_active_ms DESC LIMIT ? OFFSET ?",
                    (page_limit, page_offset),
                ).fetchall()
            return (int(total_row["n"]) if total_row else 0), [
                PendingViewerSummary.model_validate(dict(row)) for row in rows
            ]

        return await self._run_in_executor(_exec)

    async def keep_separate(self, *, viewer_id: int) -> bool:
        """保持新建：清除待确认标记；观众不在待确认状态时返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE viewers SET link_status='confirmed', link_candidate_id=NULL"
                    " WHERE id=? AND link_status='pending'",
                    (int(viewer_id),),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def merge(self, *, source_id: int, target_id: int) -> None:
        """把 ``source_id`` 并入 ``target_id``（来源观众行删除）；不成立时抛 ``ViewerIdentityError``。"""

        def _exec() -> None:
            with self._manager.transaction(immediate=True) as conn:
                src, dst = _load(conn, source_id), _load(conn, target_id)
                if src is None or dst is None:
                    raise ViewerIdentityError("观众不存在")
                if int(src["id"]) == int(dst["id"]):
                    raise ViewerIdentityError("不能把观众并入他自己")
                if src["platform"] != dst["platform"]:
                    raise ViewerIdentityError("不同平台的观众不能合并")
                if int(src["simulated"]) != int(dst["simulated"]):
                    raise ViewerIdentityError("模拟观众与真实观众不能合并")
                for column, label in _ID_LABELS.items():
                    if src[column] and dst[column] and src[column] != dst[column]:
                        raise ViewerIdentityError(f"两位观众的{label}都有值且不同，是两个不同账号，不能合并")
                _merge_rows(conn, src, dst)

        await self._run_in_executor(_exec)

    async def unlink(self, *, viewer_id: int, id_kind: IdKind) -> int:
        """把观众身上的一个外部 ID 拆成新观众，返回新观众 ID；不成立时抛 ``ViewerIdentityError``。"""
        column, other = ("account_id", "open_id") if id_kind == "account" else ("open_id", "account_id")

        def _exec() -> int:
            with self._manager.transaction(immediate=True) as conn:
                row = _load(conn, viewer_id)
                if row is None:
                    raise ViewerIdentityError("观众不存在")
                if not row[column]:
                    raise ViewerIdentityError(f"这位观众没有{_ID_LABELS[column]}")
                if not row[other]:
                    raise ViewerIdentityError("这位观众只剩这一个 ID，没有可拆的")
                return _split_id(conn, row, id_kind, column)

        return await self._run_in_executor(_exec)


# -------------------- 事务内的同步步骤 --------------------


def _load(conn: sqlite3.Connection, viewer_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM viewers WHERE id=?", (int(viewer_id),)).fetchone()
    return dict(row) if row else None


def _merge_rows(conn: sqlite3.Connection, src: Dict[str, Any], dst: Dict[str, Any]) -> None:
    """合并的落库步骤：外部 ID、计数与昵称、昵称记录、明细、画像、其他观众的并入建议。"""
    src_id, dst_id = int(src["id"]), int(dst["id"])
    # 先清空来源行的外部 ID，再写到目标行，避免两行同时持有同一 ID 撞唯一约束
    conn.execute("UPDATE viewers SET account_id=NULL, open_id=NULL, open_app_id=NULL WHERE id=?", (src_id,))
    open_src = dst if dst["open_id"] else src
    newer = src if int(src["name_seen_ms"]) > int(dst["name_seen_ms"]) else dst
    conn.execute(
        "UPDATE viewers SET account_id=?, open_id=?, open_app_id=?, user_name=?, name_seen_ms=?,"
        + ", ".join(f" {col}={col}+?" for col in _COUNT_COLUMNS)
        + ", last_active_ms=MAX(last_active_ms, ?) WHERE id=?",
        (
            dst["account_id"] or src["account_id"],
            open_src["open_id"],
            open_src["open_app_id"],
            newer["user_name"],
            int(newer["name_seen_ms"]),
            *(int(src[col]) for col in _COUNT_COLUMNS),
            int(src["last_active_ms"]),
            dst_id,
        ),
    )
    for name_row in conn.execute(
        "SELECT user_name, first_seen_ms, last_seen_ms FROM viewer_names WHERE viewer_id=?", (src_id,)
    ).fetchall():
        conn.execute(
            "INSERT INTO viewer_names(viewer_id, user_name, first_seen_ms, last_seen_ms) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(viewer_id, user_name) DO UPDATE SET"
            " first_seen_ms=MIN(first_seen_ms, excluded.first_seen_ms),"
            " last_seen_ms=MAX(last_seen_ms, excluded.last_seen_ms)",
            (dst_id, name_row["user_name"], int(name_row["first_seen_ms"]), int(name_row["last_seen_ms"])),
        )
    conn.execute("DELETE FROM viewer_names WHERE viewer_id=?", (src_id,))
    for table, *_ in _DETAIL_TABLES:
        conn.execute(f"UPDATE {table} SET viewer_id=? WHERE viewer_id=?", (dst_id, src_id))  # noqa: S608 常量表名
    # 画像以目标为准；目标还没有画像时沿用来源的
    if conn.execute("SELECT 1 FROM viewer_profiles WHERE viewer_id=?", (dst_id,)).fetchone() is None:
        conn.execute("UPDATE viewer_profiles SET viewer_id=? WHERE viewer_id=?", (dst_id, src_id))
    else:
        conn.execute("DELETE FROM viewer_profiles WHERE viewer_id=?", (src_id,))
    # 目标自己正等着并入来源：人工已经反向处理，这条建议作废；其他建议并入来源的改为建议并入目标
    conn.execute(
        "UPDATE viewers SET link_status='confirmed', link_candidate_id=NULL WHERE id=? AND link_candidate_id=?",
        (dst_id, src_id),
    )
    conn.execute("UPDATE viewers SET link_candidate_id=? WHERE link_candidate_id=?", (dst_id, src_id))
    conn.execute("DELETE FROM viewers WHERE id=?", (src_id,))


def _split_id(conn: sqlite3.Connection, row: Dict[str, Any], id_kind: IdKind, column: str) -> int:
    """取消关联的落库步骤，返回新观众 ID。"""
    orig_id = int(row["id"])
    external_id = str(row[column])
    open_app_id = row["open_app_id"] if id_kind == "open" else None
    if id_kind == "account":
        conn.execute("UPDATE viewers SET account_id=NULL WHERE id=?", (orig_id,))
    else:
        conn.execute("UPDATE viewers SET open_id=NULL, open_app_id=NULL WHERE id=?", (orig_id,))
    cur = conn.execute(
        "INSERT INTO viewers(platform, account_id, open_id, open_app_id, user_name, name_seen_ms, simulated,"
        " last_active_ms) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (
            row["platform"],
            external_id if id_kind == "account" else None,
            external_id if id_kind == "open" else None,
            open_app_id,
            row["user_name"],
            int(row["simulated"]),
            now_ms(),
        ),
    )
    new_id = int(cur.lastrowid or 0)
    # 明细里原始外部 ID 与种类都对得上的行，改指新观众
    for table, id_column, *_ in _DETAIL_TABLES:
        conn.execute(
            f"UPDATE {table} SET viewer_id=? WHERE viewer_id=? AND id_kind=? AND {id_column}=?",  # noqa: S608
            (new_id, orig_id, id_kind, external_id),
        )
    moved = _detail_stats(conn, new_id)
    # 新观众：计数按改指过来的明细重算，昵称取这些明细里最近的一个
    conn.execute(
        "UPDATE viewers SET " + ", ".join(f"{col}=?" for col in _COUNT_COLUMNS) + ", last_active_ms=? WHERE id=?",
        (*(moved[col] for col in _COUNT_COLUMNS), moved["last_active_ms"] or now_ms(), new_id),
    )
    # 原观众：减去同样的数（历史累计里拆不出来的部分留在原观众身上）
    conn.execute(
        "UPDATE viewers SET " + ", ".join(f"{col}=MAX({col}-?, 0)" for col in _COUNT_COLUMNS) + " WHERE id=?",
        (*(moved[col] for col in _COUNT_COLUMNS), orig_id),
    )
    _move_names(conn, orig_id, new_id, row)
    return new_id


def _detail_stats(conn: sqlite3.Connection, viewer_id: int) -> Dict[str, int]:
    """按明细重算一位观众的计数，口径与记账器一致（发言只算弹幕，付费排除免费币种）。"""

    def one(sql: str) -> int:
        return int(conn.execute(sql, (viewer_id,)).fetchone()[0] or 0)

    message = one("SELECT COUNT(*) FROM live_chat WHERE viewer_id=? AND message_type='danmaku'")
    gift = one("SELECT COUNT(*) FROM gifts WHERE viewer_id=?")
    paid_count = paid_amount = 0
    for table in ("gifts", "super_chats", "guards"):
        row = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(total_price), 0) FROM {table}"  # noqa: S608 常量表名
            " WHERE viewer_id=? AND currency NOT IN (?, ?)",
            (viewer_id, *_UNPAID_CURRENCIES),
        ).fetchone()
        paid_count += int(row[0])
        paid_amount += int(row[1])
    replied = one(
        "SELECT COUNT(*) FROM live_chat WHERE sender_role='assistant' AND reply_to_message_id IN"
        " (SELECT message_id FROM live_chat WHERE viewer_id=? AND message_id IS NOT NULL AND message_id<>'')"
    )
    interaction = message + gift + replied
    if interaction == 0 and paid_count:
        interaction = 1  # 只付过费的观众，付费即一次互动
    last_active = 0
    for table, *_ in _DETAIL_TABLES:
        last_active = max(last_active, one(f"SELECT MAX(timestamp_ms) FROM {table} WHERE viewer_id=?"))  # noqa: S608
    return {
        "message_count": message,
        "gift_count": gift,
        "replied_count": replied,
        "interaction_count": interaction,
        "paid_count": paid_count,
        "paid_amount": paid_amount,
        "last_active_ms": last_active,
    }


def _detail_names(conn: sqlite3.Connection, viewer_id: int) -> List[Tuple[str, int, int]]:
    """明细里这位观众用过的昵称及最早、最晚出现时刻（最近出现的在前）。"""
    union = " UNION ALL ".join(
        f"SELECT {name_column} AS name, timestamp_ms AS ts FROM {table} WHERE viewer_id=?{extra}"
        for table, _, name_column, extra in _DETAIL_TABLES
    )
    rows = conn.execute(
        f"SELECT name, MIN(ts) AS first_ms, MAX(ts) AS last_ms FROM ({union})"  # noqa: S608 常量片段
        " WHERE name IS NOT NULL AND name<>'' GROUP BY name ORDER BY last_ms DESC",
        (viewer_id,) * len(_DETAIL_TABLES),
    ).fetchall()
    return [(str(r["name"]), int(r["first_ms"]), int(r["last_ms"])) for r in rows]


def _move_names(conn: sqlite3.Connection, orig_id: int, new_id: int, orig: Dict[str, Any]) -> None:
    """拆分后的昵称：新观众取改指明细里的昵称；原观众去掉只在那些明细里出现过的昵称。"""
    moved = _detail_names(conn, new_id)
    remaining = {name for name, _, _ in _detail_names(conn, orig_id)}
    for name, first_ms, last_ms in moved:
        conn.execute(
            "INSERT INTO viewer_names(viewer_id, user_name, first_seen_ms, last_seen_ms) VALUES (?, ?, ?, ?)",
            (new_id, name, first_ms, last_ms),
        )
        if name not in remaining:
            conn.execute("DELETE FROM viewer_names WHERE viewer_id=? AND user_name=?", (orig_id, name))
    if moved:
        latest_name, _, latest_ms = moved[0]
        conn.execute("UPDATE viewers SET user_name=?, name_seen_ms=? WHERE id=?", (latest_name, latest_ms, new_id))
    # 原观众的当前昵称若只出现在被拆走的明细里（错关联进来的人用的名字），改回它剩余明细里最近的昵称
    orig_name = str(orig["user_name"])
    if orig_name in {name for name, _, _ in moved} and orig_name not in remaining:
        latest_remaining = _detail_names(conn, orig_id)
        if latest_remaining:
            name, _, last_ms = latest_remaining[0]
            conn.execute("UPDATE viewers SET user_name=?, name_seen_ms=? WHERE id=?", (name, last_ms, orig_id))


__all__ = ["ViewerIdentityError", "ViewerIdentityRepo"]
