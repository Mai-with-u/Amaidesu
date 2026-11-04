"""v23 → v24：场次整理进度三列，下播整理据此补跑与人工重整理。

新账本（承诺、场次档案）由建库 DDL 建两张表，迁移回调不重复建；这里只补
``live_sessions`` 的进度列：

- ``settled_at_ms``：本场整理完成的时刻（空 = 待整理）；
- ``settle_attempts``：自动重试次数，到上限后不再自动补跑，等运营者在 WebUI
  点"重新整理"清零；
- ``settle_report``：失败原因，或成功时那些没能登记的条目（JSON），WebUI 展示。

存量场次标成已整理（``settled_at_ms = ended_at_ms``）：升级前各场走的是旧的
收播整理，不该在升级后把历史场次集中补跑一遍模型调用。只有最近一场够长的正式场
留着待整理：旧记忆在同一次升级里退役（v25 移进备份），新账本一行都没有，升级后
第一场开播她就什么都不记得；留这一场给启动时的补整理，账本从它的台词里长出第一份
档案与承诺，只多付一场的模型调用。"够长"与常驻简报跳过开错重开的那一小段同理。
回填只在补列时执行，重复执行不会把新写的、尚未整理完的场次误标成已整理。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

# 补列顺序无关；回填挂在 settled_at_ms 上，只补这一列时执行一次
_SETTLE_COLUMNS = (
    ("settled_at_ms", "INTEGER"),
    ("settle_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("settle_report", "TEXT"),
)

# 升级时留作待整理的那一场至少要这么长（毫秒）：开错后马上重开的一两分钟不算
_SEED_MIN_DURATION_MS = 5 * 60 * 1000


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：补整理进度三列，存量已结束场次视为已整理。"""
    if not table_exists(conn, "live_sessions"):
        return
    for column, column_type in _SETTLE_COLUMNS:
        if column_exists(conn, "live_sessions", column):
            continue
        conn.execute(f"ALTER TABLE live_sessions ADD COLUMN {column} {column_type}")  # noqa: S608 列名为代码内常量
        if column == "settled_at_ms":
            conn.execute("UPDATE live_sessions SET settled_at_ms=ended_at_ms WHERE ended_at_ms IS NOT NULL")
            _leave_latest_live_session_pending(conn)


def _leave_latest_live_session_pending(conn: sqlite3.Connection) -> None:
    """最近一场够长的已结束正式场改回待整理，升级后启动时由下播整理补一份档案与承诺。"""
    row = conn.execute(
        "SELECT id FROM live_sessions WHERE kind='live' AND ended_at_ms IS NOT NULL"
        " AND ended_at_ms - started_at_ms >= ? ORDER BY started_at_ms DESC, id DESC LIMIT 1",
        (_SEED_MIN_DURATION_MS,),
    ).fetchone()
    if row is not None:
        conn.execute("UPDATE live_sessions SET settled_at_ms=NULL WHERE id=?", (int(row[0]),))


__all__ = ["migrate"]
