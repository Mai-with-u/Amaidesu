"""v24 → v25：记忆改按账本记录之后，删掉旧的记忆存储。

这些都是账本上线后就不再被写入的旧结构（场次回顾、观众画像、本场备忘录），也不再进
任何提示词。本版是不可逆清理：

- ``session_recaps``：每场一份收播总结，已被场次档案与承诺账本取代；
- ``viewer_profiles``：观众印象文本，不再生成也不再注入；
- ``live_sessions.notebook_text``：本场备忘录全文，备忘录工具已下线。

旧长期记忆还把 ``MEMORY.md`` 与每次改写的历史留底写在库文件旁的 ``memory/`` 目录，
里面含观众昵称。这一版不直接删除它，而是整目录移进库文件旁的 ``backups/``（与迁移前
整库备份同一位置，目录名 ``memory-pre-v25-<时间戳>``）：升级后没有任何代码再读它，
用户确认存量可以清除，但移走而不是删掉，误升级与事后反查都还能取回。

删表在前、删列在后；重复执行安全（存在性检查）。v18 迁移里重建 ``viewer_profiles``
用的建表语句已冻结在该文件内，不依赖 schema.py。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from src.modules.logging import get_logger
from src.modules.storage.migrations._common import column_exists, table_exists

logger = get_logger("storage.migrations.v25")

# 删掉的旧记忆表
_DROPPED_TABLES = ("session_recaps", "viewer_profiles")


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：删旧记忆的两张表与场次表上的备忘录列。"""
    for table in _DROPPED_TABLES:
        if table_exists(conn, table):
            conn.execute(f"DROP TABLE {table}")  # noqa: S608 表名为代码内常量
    if table_exists(conn, "live_sessions") and column_exists(conn, "live_sessions", "notebook_text"):
        conn.execute("ALTER TABLE live_sessions DROP COLUMN notebook_text")
    _archive_legacy_memory_dir(conn)


def _archive_legacy_memory_dir(conn: sqlite3.Connection) -> None:
    """把库文件旁的旧记忆目录整目录移进备份目录（``MEMORY.md`` 与历史留底快照）。

    目标与迁移前整库备份同一个 ``backups/`` 目录，名字带版本与时刻，便于事后认出
    是哪一次升级移走的。移动（同一卷内改名）而不是复制或删除：升级后目录不再被任何
    代码读取，但用户误升级、想反查旧笔记时还能从备份里取回；移走失败时目录原地保留，
    只记告警、不阻塞升级。目录不存在（全新库或已移过）时什么也不做。
    """
    db_file = _main_db_file(conn)
    if db_file is None:
        return
    memory_dir = db_file.parent / "memory"
    if not memory_dir.is_dir():
        return
    backup_dir = db_file.parent / "backups"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_memory_backup_target(backup_dir)
        os.replace(memory_dir, target)
    except OSError as exc:  # 移不走不挡升级：目录原地保留，人工可手工处理
        logger.warning(f"旧记忆目录移入备份失败，目录原地保留、未删除: {memory_dir} ({exc})", exc=exc)
        return
    logger.info(f"旧记忆目录已移入迁移备份（未删除，可手工取回）: {memory_dir} -> {target}")
    # 改名不更新目录自身的修改时刻，而保留期按这个时刻判定备份有多旧：不重置的话，
    # 几个月没动过的旧记忆目录一移进来就已"过期"，同一次启动随后的保留期清理会把它删掉
    try:
        os.utime(target)
    except OSError as exc:  # 重置失败只影响保留期起算，目录已安全移入备份
        logger.warning(f"旧记忆备份目录的修改时刻重置失败（保留期可能提前删除它）: {target} ({exc})", exc=exc)


def _main_db_file(conn: sqlite3.Connection) -> Path | None:
    """当前连接的库文件路径；内存库或取不到路径时返回 None（迁移此时不动磁盘）。"""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if len(row) >= 3 and row[1] == "main" and row[2]:
            db_file = str(row[2])
            if db_file == ":memory:":
                return None
            return Path(db_file)
    return None


def _unique_memory_backup_target(backup_dir: Path) -> Path:
    """给旧记忆目录挑一个不重名的备份落点（时刻相同则追加序号，不覆盖已有备份）。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")
    target = backup_dir / f"memory-pre-v25-{stamp}"
    suffix = 0
    while target.exists():
        suffix += 1
        target = backup_dir / f"memory-pre-v25-{stamp}-{suffix}"
    return target


__all__ = ["migrate"]
