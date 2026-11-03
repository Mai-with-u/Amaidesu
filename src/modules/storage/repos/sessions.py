"""SessionRepo —— live_sessions 场次表仓储。

场次行由 LiveSessionManager 创建与结账：一行 = 一场直播（有开始/结束边界），
主键 AUTOINCREMENT；房间/频道是普通属性列。本仓储只提供行级领域方法，
不持有"当前场次"状态。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from src.modules.storage.models.memory import CommitmentClosure, PendingArchiveLine, PendingCommitment
from src.modules.storage.models.sessions import LiveSession, LiveSessionListItem
from src.modules.storage.repos._base import BaseRepo
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind


# 下播整理的自动重试上限：用满后不再自动补跑，等运营者在 WebUI 点「重新整理」把次数清零。
# 定在存储层：整理执行器与 WebUI 的整理状态接口共用同一个数，不各写一份
MAX_SETTLE_ATTEMPTS = 3


# 下播整理的自动重试上限：用满后不再自动补跑，等运营者在 WebUI 点「重新整理」把次数清零。
# 定在存储层：整理执行器与 WebUI 的整理状态接口共用同一个数，不各写一份
MAX_SETTLE_ATTEMPTS = 3


class SessionRepo(BaseRepo):
    """live_sessions 场次表的读写。"""

    async def insert_live_session(
        self,
        *,
        stream_id: str = "",
        platform: str = "unknown",
        started_at_ms: int,
        title: Optional[str] = None,
        source: str = "manual",
        kind: SessionKind = SESSION_KIND_LIVE,
    ) -> int:
        """插入一场新场次（``ended_at_ms`` 为 NULL 即进行中），返回场次主键。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO live_sessions ("
                    "stream_id, platform, started_at_ms, title, source, kind, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (stream_id, platform, started_at_ms, title, source, kind, started_at_ms),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def close_live_session(self, *, live_session_id: int, ended_at_ms: int) -> bool:
        """写入场次结束时间（幂等：已结束的场次不覆盖）。返回是否命中行。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET ended_at_ms=?, updated_at_ms=? WHERE id=? AND ended_at_ms IS NULL",
                    (ended_at_ms, ended_at_ms, live_session_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def update_live_session_stats(
        self,
        *,
        live_session_id: int,
        heat: int,
        viewer_count: int,
        audience_total: int,
        updated_at_ms: int,
    ) -> bool:
        """更新场次实时状态（热度/计数心跳）。行不存在（如默认场次被清理）返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET heat=?, viewer_count=?, audience_total=?, updated_at_ms=? WHERE id=?",
                    (heat, viewer_count, audience_total, updated_at_ms, live_session_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def update_live_session_title(
        self,
        *,
        live_session_id: int,
        title: Optional[str],
        updated_at_ms: int,
    ) -> bool:
        """改写场次标题（重命名；None 即清除标题）。行不存在返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET title=?, updated_at_ms=? WHERE id=?",
                    (title, updated_at_ms, live_session_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def update_live_session_kind(
        self,
        *,
        live_session_id: int,
        kind: SessionKind,
        updated_at_ms: int,
    ) -> bool:
        """更正已结账场次的类型（存量回填判错时由人手改）。进行中或不存在的场次返回 False。

        进行中的场次不改：类型在开场时声明，主播记忆与对外同步已按它分流。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET kind=?, updated_at_ms=? WHERE id=? AND ended_at_ms IS NOT NULL",
                    (kind, updated_at_ms, live_session_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def set_session_notebook(self, *, live_session_id: int, notebook_text: str) -> bool:
        """把进行中场次的备忘录全文写进场次行（每次写备忘录都整份覆盖）；已结账或不存在返回 False。

        已结账的场次不再改：收播后备忘录已清空，迟到的写入不该覆盖这场留下的结论。
        不动 ``updated_at_ms``——它记的是心跳统计的更新时刻。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET notebook_text=? WHERE id=? AND ended_at_ms IS NULL",
                    (notebook_text, live_session_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def list_recapped_session_ids(self, *, kind: SessionKind) -> List[int]:
        """某类场次里写过回顾的场次主键（升序）；长期笔记按它数"隔了几场"。

        没写成回顾的场次（空场次、整理失败）不算一场：主播在那几场里没留下可回忆的东西。
        """

        def _exec() -> List[int]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT s.id FROM live_sessions s JOIN session_recaps r ON r.live_session_id = s.id"
                    " WHERE s.kind=? ORDER BY s.id ASC",
                    (kind,),
                ).fetchall()
                return [int(row[0]) for row in rows]

        return await self._run_in_executor(_exec)

    async def get_live_session(self, *, live_session_id: int) -> Optional[LiveSession]:
        """按场次主键查单行；未命中返回 None。"""

        def _exec() -> Optional[LiveSession]:
            with self._manager.transaction() as conn:
                row = conn.execute("SELECT * FROM live_sessions WHERE id=?", (live_session_id,)).fetchone()
                return LiveSession.model_validate(dict(row)) if row else None

        return await self._run_in_executor(_exec)

    async def list_dangling_live_sessions(self) -> List[LiveSession]:
        """列出未结账的显式场次（ended_at_ms IS NULL）。

        用于启动期收口：上次进程未正常退出的残留"进行中"场次。
        """

        def _exec() -> List[LiveSession]:
            with self._manager.transaction() as conn:
                rows = conn.execute("SELECT * FROM live_sessions WHERE ended_at_ms IS NULL").fetchall()
                return [LiveSession.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def get_last_ended_live_session_id(self) -> Optional[int]:
        """取最近一场已结束的场次主键（按结账时刻倒序）；一场都没有返回 None。

        供场次归属回退用：场后发言（上一场已结束、本场未开）归到刚结束的那场。
        空场次在结账时整行丢弃，库里没有它们的行，不会被选中。
        """

        def _exec() -> Optional[int]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT id FROM live_sessions WHERE ended_at_ms IS NOT NULL "
                    "ORDER BY ended_at_ms DESC, id DESC LIMIT 1"
                ).fetchone()
                return int(row[0]) if row else None

        return await self._run_in_executor(_exec)

    async def delete_live_session(self, *, live_session_id: int) -> bool:
        """删除场次行并级联清除其明细数据。

        级联范围：live_chat / gifts / super_chats / guards / topics / game_events /
        timeline_summary / session_recaps / archive_lines（均以 ``live_session_id``
        引用场次主键）。承诺不随场次删除：它自带原话，删掉场次后仍能照着标题与引文
        展示，只是点回原句时找不到台词行。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                for table in (
                    "live_chat",
                    "gifts",
                    "super_chats",
                    "guards",
                    "topics",
                    "game_events",
                    "timeline_summary",
                    "session_recaps",
                    "archive_lines",
                ):
                    conn.execute(f"DELETE FROM {table} WHERE live_session_id=?", (live_session_id,))  # noqa: S608 表名为代码内常量
                cur = conn.execute("DELETE FROM live_sessions WHERE id=?", (live_session_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def list_sessions_pending_settle(self, *, limit: int = 4, max_attempts: int = 3) -> List[LiveSession]:
        """待下播整理的正式场（开播时刻从早到晚）：已结束、未整理完、自动重试没用完。

        彩排场永远不在候选里；重试用满的场次留给运营者在 WebUI 点「重新整理」，
        免得模型中转站挂一阵就把这一场的承诺与档案永久丢掉。
        """

        def _exec() -> List[LiveSession]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT * FROM live_sessions"
                    " WHERE ended_at_ms IS NOT NULL AND settled_at_ms IS NULL"
                    "   AND kind=? AND settle_attempts<?"
                    " ORDER BY started_at_ms ASC, id ASC LIMIT ?",
                    (SESSION_KIND_LIVE, max(0, int(max_attempts)), max(1, int(limit))),
                ).fetchall()
                return [LiveSession.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def mark_settle_done(
        self,
        *,
        live_session_id: int,
        settled_at_ms: int,
        settle_report: Optional[str] = None,
    ) -> bool:
        """记下这一场整理完成（附整理报告）；行不存在返回 False。

        不动 updated_at_ms：那一列记的是心跳统计的更新时刻。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET settled_at_ms=?, settle_report=? WHERE id=?",
                    (int(settled_at_ms), settle_report, int(live_session_id)),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def record_settle_failure(self, *, live_session_id: int, settle_report: str) -> int:
        """记一次整理失败：重试次数加一、写失败原因，返回累计次数（行不存在返回 0）。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                conn.execute(
                    "UPDATE live_sessions SET settle_attempts=settle_attempts+1, settle_report=? WHERE id=?",
                    (settle_report, int(live_session_id)),
                )
                row = conn.execute(
                    "SELECT settle_attempts FROM live_sessions WHERE id=?", (int(live_session_id),)
                ).fetchone()
                return int(row[0]) if row else 0

        return await self._run_in_executor(_exec)

    async def apply_settlement(
        self,
        *,
        live_session_id: int,
        archive_lines: List[PendingArchiveLine],
        commitments: List[PendingCommitment],
        closures: List[CommitmentClosure],
        settled_at_ms: int,
        now_ms_value: int,
        settle_report: Optional[str] = None,
    ) -> Dict[str, int]:
        """把一场的整理结果一次写进去：档案行、新承诺、了结、整理完成时刻。

        整理必须整场原子提交：半场写进去再失败，重跑时会重复登记。跨三张表的写操作
        只有这一处，事务边界放在场次仓储里，与删除场次的级联同一层。返回各类写入条数。
        """

        def _exec() -> Dict[str, int]:
            written = {"archive": 0, "commitments": 0, "closed": 0}
            with self._manager.transaction() as conn:
                for line in archive_lines:
                    conn.execute(
                        "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id) VALUES (?, ?, ?, ?)",
                        (int(live_session_id), line.text, line.quote, int(line.evidence_chat_id)),
                    )
                    written["archive"] += 1
                for item in commitments:
                    cur = conn.execute(
                        "INSERT INTO commitments(what, quote, evidence_chat_id, viewer_id, game,"
                        " created_session_id, created_at_ms, updated_at_ms)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                        " ON CONFLICT(evidence_chat_id) DO NOTHING",
                        (
                            item.what,
                            item.quote,
                            item.evidence_chat_id,
                            item.viewer_id,
                            item.game,
                            int(live_session_id),
                            int(now_ms_value),
                            int(now_ms_value),
                        ),
                    )
                    written["commitments"] += max(0, int(cur.rowcount or 0))
                for closure in closures:
                    cur = conn.execute(
                        "UPDATE commitments SET status='closed', close_chat_id=?, close_quote=?,"
                        " updated_at_ms=? WHERE id=? AND status='open'",
                        (
                            closure.close_chat_id,
                            closure.close_quote,
                            int(now_ms_value),
                            int(closure.commitment_id),
                        ),
                    )
                    written["closed"] += max(0, int(cur.rowcount or 0))
                conn.execute(
                    "UPDATE live_sessions SET settled_at_ms=?, settle_report=? WHERE id=?",
                    (int(settled_at_ms), settle_report, int(live_session_id)),
                )
            return written

        return await self._run_in_executor(_exec)

    async def reset_settle_attempts(self, *, live_session_id: int) -> bool:
        """把重试次数清零并清掉上次的失败原因，下一次整理调度就会再挑到这一场。

        运营者在 WebUI 点「重新整理」时调用；返回是否命中行。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET settle_attempts=0, settle_report=NULL WHERE id=?",
                    (int(live_session_id),),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def reset_memory_ledger_for_session(self, *, live_session_id: int) -> int:
        """清空一场在账本里的痕迹：档案行、本场登记的承诺、整理进度；返回删除条数。

        正式场改成彩排时用（与改类型在同一个调用里）：彩排场不整理，那一场留下的话
        不该进正式的账。承诺删掉是不留痕的——重新整理时会按那场的台词重新登记。
        """

        def _exec() -> int:
            with self._manager.transaction() as conn:
                removed = 0
                cur = conn.execute("DELETE FROM archive_lines WHERE live_session_id=?", (int(live_session_id),))
                removed += int(cur.rowcount or 0)
                cur = conn.execute("DELETE FROM commitments WHERE created_session_id=?", (int(live_session_id),))
                removed += int(cur.rowcount or 0)
                conn.execute(
                    "UPDATE live_sessions SET settled_at_ms=NULL, settle_attempts=0, settle_report=NULL WHERE id=?",
                    (int(live_session_id),),
                )
                return removed

        return await self._run_in_executor(_exec)

    async def list_live_sessions(
        self,
        *,
        limit: int = 50,
        source: Optional[str] = None,
        kind: Optional[SessionKind] = None,
        title_keyword: Optional[str] = None,
    ) -> List[LiveSessionListItem]:
        """列出场次（附消息数），供场次列表/回看选择。

        排序：按开始时间倒序。无显式场次期间不创建兜底行；列表只含历史
        显式场次。
        筛选：``source`` 精确匹配来源；``kind`` 精确匹配类型；``title_keyword``
        对 title 做包含匹配。

        Args:
            limit: 最多返回条数
            source: 来源过滤（manual / replay / generate / legacy）；None 不过滤
            kind: 类型过滤（live / rehearsal）；None 不过滤
            title_keyword: 标题关键字；None 或空串不过滤
        """

        conditions: List[str] = []
        params: List[object] = []
        if source:
            conditions.append("s.source=?")
            params.append(source)
        if kind:
            conditions.append("s.kind=?")
            params.append(kind)
        if title_keyword:
            conditions.append("s.title LIKE ?")
            params.append(f"%{title_keyword}%")
        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        def _exec() -> List[LiveSessionListItem]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT s.*, ("
                    "SELECT COUNT(*) FROM live_chat c WHERE c.live_session_id = s.id"
                    ") AS message_count "
                    f"FROM live_sessions s {where_clause} "
                    "ORDER BY s.started_at_ms DESC LIMIT ?",
                    params,
                ).fetchall()
                return [LiveSessionListItem.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)


__all__ = ["MAX_SETTLE_ATTEMPTS", "SessionRepo"]
