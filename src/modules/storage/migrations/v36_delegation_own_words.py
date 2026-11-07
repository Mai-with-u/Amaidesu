"""v35 → v36：旧的委派受理行只留她自己的原话；历史上落成阶段汇报的"停了没交付"连同索引删掉。

- 受理行（``delegation_accepted``）原先存委派正文全文：她的原话后面附着来源对话，里面有观众昵称、
  弹幕原话与玩家公屏原话。新写入的已只存原话，旧行在这里原地改写：取"[本次目标]"那段，去掉后面的
  "[来源对话：……]"。没按这个格式拼的正文（运营在 WebUI 直派的）整段就是原话，原样留着。
  保留期默认不删原始记录，不改写的话这些原话会一直留在库里。
- "停了没交付"的代发上报原先落成 ``report`` 行，每次都是同一句固定话，进了全文索引与向量索引，
  回想时搜出一串；新写入的已不落成游戏事件行。旧行连同两种索引一起删：只删索引不删行的话，
  索引任务会按来源行把它补回来。同一件事委派账里有 ``delegation_undelivered`` 终态行，删掉不丢事实。

正文格式按写入当时定稿：两段抬头与那句固定话在这里留一份副本，框架里的拼法以后改动，
不影响这一版迁移做过的事。幂等：改写过的行再取一遍还是原话；删过的行不再匹配。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import table_exists

# 写入当时委派正文的两段抬头：本次目标（后面紧跟她的原话）与附带的来源对话（前面空一行）
_DELEGATE_HEAD = "[本次目标]\n"
_DELEGATE_DIALOGUE_HEAD = "\n\n[来源对话："
# 写入当时代发"停了没交付"的那句固定话
_UNDELIVERED_REPORT_MESSAGE = "游戏那边停了，没交付结果，做没做完不确定"


def _own_words(instruction: str) -> str:
    """委派正文里她自己的原话：按上面的格式拼过的取出本次目标那一段，没拼过的原样返回。"""
    head, sep, rest = instruction.partition(_DELEGATE_HEAD)
    if head or not sep:
        return instruction
    own, sep, _ = rest.partition(_DELEGATE_DIALOGUE_HEAD)
    return own if sep else instruction


def _rewrite_accepted_rows(conn: sqlite3.Connection) -> None:
    """受理行改写成只留原话（委派账行不进索引，改正文触发的删索引对它们是空操作）。"""
    rows = conn.execute("SELECT id, message FROM game_events WHERE event_type = 'delegation_accepted'").fetchall()
    for row in rows:
        row_id, message = row[0], str(row[1] or "")
        own = _own_words(message)
        if own != message:
            conn.execute("UPDATE game_events SET message = ? WHERE id = ?", (own, row_id))


def _drop_undelivered_reports(conn: sqlite3.Connection) -> None:
    """删掉"停了没交付"的阶段汇报行：先删两种索引（不依赖外键开关与触发器是否在），再删来源行。"""
    stale = "SELECT id FROM game_events WHERE event_type = 'report' AND message = ?"
    params = (_UNDELIVERED_REPORT_MESSAGE,)
    if table_exists(conn, "memory_fts_event"):
        conn.execute(f"DELETE FROM memory_fts_event WHERE rowid IN ({stale})", params)  # noqa: S608 常量片段
    if table_exists(conn, "memory_vectors_event"):
        conn.execute(f"DELETE FROM memory_vectors_event WHERE event_id IN ({stale})", params)  # noqa: S608 常量片段
    conn.execute("DELETE FROM game_events WHERE event_type = 'report' AND message = ?", params)


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：旧受理行只留原话 → 删"停了没交付"的阶段汇报行及其索引；没有游戏事件表时什么都不做。"""
    if not table_exists(conn, "game_events"):
        return
    _rewrite_accepted_rows(conn)
    _drop_undelivered_reports(conn)


__all__ = ["migrate"]
