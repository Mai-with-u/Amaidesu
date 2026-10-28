"""v18 → v19：``live_sessions`` 加场次类型列 ``kind``（正式 / 彩排）。

此前场次只记来源（谁开的场），不记"这场的观众是不是真人"。回放场次、模拟器
生成场次、控制台调试场次在库里和真直播一模一样，收播都写回顾，下一场开播把
它当"上一场直播"注入。新列 ``kind``：``live`` = 正式（面向真实观众），
``rehearsal`` = 彩排（模拟、回放、调试）。

存量回填只在有明确证据时把场次判为彩排，其余保持正式（与旧行为一致）：

- 来源为模拟器回放（``source='replay'``）；
- 或：本场有观众明细（弹幕 / 礼物 / SC / 上舰），但没有一条来自真实平台的
  真实观众（全是模拟数据，或只有控制台调试输入），且没收到过平台的观看数
  推送（``audience_total=0``）。

没有任何观众明细的场次（例如一场没人发弹幕的真直播）判不出真假，保持正式；
判错的场次可在 WebUI 场次页手动更正。

回填只在本次补列时执行一次：列已存在（新库或已迁移过）时整段跳过，重复执行
不会覆盖用户在 WebUI 里的手动更正。
"""

from __future__ import annotations

import sqlite3
from typing import List, Tuple

from src.modules.storage.migrations._common import column_exists, table_exists

# 观众明细表与"这一行来自观众"的附加条件（live_chat 里还有主播与联动对象的行）
_VIEWER_DETAIL_TABLES: Tuple[Tuple[str, str], ...] = (
    ("live_chat", " AND d.sender_role='viewer'"),
    ("gifts", ""),
    ("super_chats", ""),
    ("guards", ""),
)
# 调试输入的平台名：控制台输入不带模拟标记，靠平台名与真实观众区分
_DEBUG_PLATFORMS = "('', 'console')"


def _exists_clauses(conn: sqlite3.Connection, *, real_only: bool) -> List[str]:
    """拼"本场存在观众明细"的 EXISTS 子句；``real_only`` 时只认真实平台的真实观众。"""
    clauses: List[str] = []
    for table, viewer_filter in _VIEWER_DETAIL_TABLES:
        if not table_exists(conn, table):
            continue
        real_filter = ""
        if real_only:
            if not (column_exists(conn, table, "simulated") and column_exists(conn, table, "platform")):
                continue
            real_filter = f" AND d.simulated=0 AND d.platform NOT IN {_DEBUG_PLATFORMS}"
        clauses.append(
            f"EXISTS (SELECT 1 FROM {table} d WHERE d.live_session_id=s.id{viewer_filter}{real_filter})"  # noqa: S608 表名与条件均为代码内常量
        )
    return clauses


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：补 ``kind`` 列，并对存量场次按证据回填彩排。"""
    if not table_exists(conn, "live_sessions"):
        return
    if column_exists(conn, "live_sessions", "kind"):
        return
    conn.execute(
        "ALTER TABLE live_sessions ADD COLUMN kind TEXT NOT NULL DEFAULT 'live' CHECK (kind IN ('live', 'rehearsal'))"
    )
    # 模拟器回放场次：一场回放天然是彩排
    conn.execute("UPDATE live_sessions SET kind='rehearsal' WHERE source='replay'")
    # 有观众明细、却没有一条来自真实观众，且没收到过平台观看数推送 → 彩排
    any_viewer = _exists_clauses(conn, real_only=False)
    real_viewer = _exists_clauses(conn, real_only=True)
    if not any_viewer:
        return
    condition = "(" + " OR ".join(any_viewer) + ")"
    if real_viewer:
        condition += " AND NOT (" + " OR ".join(real_viewer) + ")"
    if column_exists(conn, "live_sessions", "audience_total"):
        condition += " AND s.audience_total=0"
    conn.execute(
        f"UPDATE live_sessions SET kind='rehearsal' WHERE id IN (SELECT s.id FROM live_sessions s WHERE {condition})"  # noqa: S608 条件为代码内常量拼接
    )


__all__ = ["migrate"]
