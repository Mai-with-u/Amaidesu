"""v32 → v33：记忆页改动记录记下是哪一条候选。

批准、驳回观众候选时，改动记录原先只记标题，驳回的事后查不到是哪一条。这一版给 ``memory_edits``
加一列 ``candidate_id``（候选编号，不挂外键：候选被删后编号留着，审计照样看得到"那一条被删了"），
并加一个触发器：候选行被删时（删观众、"忘了我"、按句删、改彩排、删场次），这条候选的改动记录
改前改后的文本一并清空，动作与时刻留着——候选的标题和原话可能提到被忘掉的人。

列与触发器只在这里补：启动时先执行建表汇总、后跑迁移，建表汇总执行那一刻旧库里还没有这一列，
触发器引用它会失败；新库同样跑完整条迁移链，所以触发器也只建在这里。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, table_exists

_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_memory_edits_candidate_deleted AFTER DELETE ON viewer_candidates BEGIN
    UPDATE memory_edits SET before_text = '', after_text = '' WHERE candidate_id = OLD.id;
END
"""


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：改动记录补候选编号一列 → 候选删除时清空它的改动记录文本。"""
    if not table_exists(conn, "memory_edits"):
        return
    if not column_exists(conn, "memory_edits", "candidate_id"):
        conn.execute("ALTER TABLE memory_edits ADD COLUMN candidate_id INTEGER")
    if table_exists(conn, "viewer_candidates"):
        conn.execute(_TRIGGER_SQL)
