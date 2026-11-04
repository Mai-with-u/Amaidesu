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
from src.modules.storage.repos.archive import delete_archive_lines_by_session
from src.modules.storage.repos.commitments import (
    delete_commitments_by_session,
    reopen_commitments_closed_by_session,
)
from src.modules.types.session_kind import SESSION_KIND_LIVE, SESSION_KIND_REHEARSAL, SessionKind


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

    async def set_live_session_kind_with_ledger(
        self,
        *,
        live_session_id: int,
        kind: SessionKind,
        updated_at_ms: int,
    ) -> Dict[str, int]:
        """更正已结账场次的类型，并在同一个事务里联动账本。

        改成彩排：删掉本场档案行与本场登记的承诺、把本场台词了结掉的旧承诺放回未了结、
        清空整理进度——彩排场不整理，那一场留下的话不该进正式的账。改成正式场：只把「迁移时被标记为已整理、
        其实从没真正整理过」的场次放回待整理队列。v24 迁移给存量已结束场次写的
        整理完成时刻等于结账时刻；真实整理发生在结账之后，两者不会相等。真正的
        历史正式场保持已整理，不在升级后集中补跑。
        进行中或不存在的场次返回全 0。
        """

        def _exec() -> Dict[str, int]:
            result = {"changed": 0, "ledger_removed": 0, "reopened": 0, "requeued": 0}
            with self._manager.transaction(immediate=True) as conn:
                row = conn.execute(
                    "SELECT kind, ended_at_ms, settled_at_ms FROM live_sessions WHERE id=?",
                    (int(live_session_id),),
                ).fetchone()
                if row is None or row["ended_at_ms"] is None:
                    return result
                cur = conn.execute(
                    "UPDATE live_sessions SET kind=?, updated_at_ms=? WHERE id=? AND ended_at_ms IS NOT NULL",
                    (kind, int(updated_at_ms), int(live_session_id)),
                )
                if cur.rowcount <= 0:
                    return result
                result["changed"] = 1
                if kind == SESSION_KIND_REHEARSAL:
                    result["ledger_removed"] = delete_archive_lines_by_session(
                        conn, live_session_id=int(live_session_id)
                    ) + delete_commitments_by_session(conn, live_session_id=int(live_session_id))
                    # 彩排台词做出的了结也作废：被它了结的旧承诺放回未了结
                    result["reopened"] = reopen_commitments_closed_by_session(
                        conn, live_session_id=int(live_session_id), updated_at_ms=int(updated_at_ms)
                    )
                    conn.execute(
                        "UPDATE live_sessions SET settled_at_ms=NULL, settle_attempts=0, settle_report=NULL WHERE id=?",
                        (int(live_session_id),),
                    )
                elif row["settled_at_ms"] is not None and int(row["settled_at_ms"]) == int(row["ended_at_ms"]):
                    conn.execute(
                        "UPDATE live_sessions SET settled_at_ms=NULL, settle_attempts=0, settle_report=NULL WHERE id=?",
                        (int(live_session_id),),
                    )
                    result["requeued"] = 1
            return result

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
        timeline_summary / archive_lines（均以 ``live_session_id`` 引用场次主键）。
        承诺不随场次删除：它自带原话，删掉场次后仍能照着标题与引文展示，只是点回原句
        时找不到台词行。
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
                    "archive_lines",
                ):
                    conn.execute(f"DELETE FROM {table} WHERE live_session_id=?", (live_session_id,))  # noqa: S608 表名为代码内常量
                cur = conn.execute("DELETE FROM live_sessions WHERE id=?", (live_session_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def list_sessions_pending_settle(
        self, *, limit: int = 4, max_attempts: int = MAX_SETTLE_ATTEMPTS
    ) -> List[LiveSession]:
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

    async def count_sessions_pending_settle(self, *, max_attempts: int = MAX_SETTLE_ATTEMPTS) -> int:
        """待下播整理的正式场数（与 :meth:`list_sessions_pending_settle` 同一判据）。

        WebUI 整理状态页只显示一个数，不该为了它把待整理场次整行读出来。
        """

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM live_sessions"
                    " WHERE ended_at_ms IS NOT NULL AND settled_at_ms IS NULL"
                    "   AND kind=? AND settle_attempts<?",
                    (SESSION_KIND_LIVE, max(0, int(max_attempts))),
                ).fetchone()
                return int(row[0]) if row else 0

        return await self._run_in_executor(_exec)

    async def mark_settle_done(
        self,
        *,
        live_session_id: int,
        settled_at_ms: int,
        settle_report: Optional[str] = None,
    ) -> bool:
        """记下这一场正式场的整理完成（附整理报告）；行不存在或已不是正式场返回 False。

        条件更新带 kind='live'：整理途中场次被改成彩排时不能再补写完成时刻。
        不动 updated_at_ms：那一列记的是心跳统计的更新时刻。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET settled_at_ms=?, settle_report=? WHERE id=? AND kind=?",
                    (int(settled_at_ms), settle_report, int(live_session_id), SESSION_KIND_LIVE),
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

        整理必须整场原子提交：任何一步失败都回滚，不能让这一场留下半份结果。
        跨三张表的写操作只有这一处，事务边界放在场次仓储里，与删除场次的级联同一层。

        幂等：先用「settled_at_ms 为空且仍是正式场」抢占本场——同一场被两次整理同时
        挑中时只有一次写进去（begin immediate 把第二个写事务挡在对方提交之后，重读即
        看到已完成）；整理途中场次被改成彩排时，这里也写不进去。运营者重置进度后重跑
        时，同一事务里先删掉本场旧档案行、把上一轮用本场台词了结的旧承诺放回未了结，
        再写，重跑整场替换：不会越堆越多，上一轮认定、这一轮不再认定的了结也不会留在
        账上。本场登记的未了结承诺按原话那句台词对齐——这一轮还认定的保留原编号、原地
        更新，不再认定的才删；运营者已了结或判误记的承诺不受影响。
        返回各类写入条数（claimed = 1 表示这次真的抢到并写了）。
        """

        def _exec() -> Dict[str, int]:
            written = {"archive": 0, "commitments": 0, "closed": 0, "claimed": 0}
            with self._manager.transaction(immediate=True) as conn:
                claim = conn.execute(
                    "UPDATE live_sessions SET settled_at_ms=?, settle_report=?"
                    " WHERE id=? AND settled_at_ms IS NULL AND kind=?",
                    (int(settled_at_ms), settle_report, int(live_session_id), SESSION_KIND_LIVE),
                )
                if claim.rowcount <= 0:
                    return written
                written["claimed"] = 1
                # 重跑整场替换：旧档案行全清。本场登记、仍未了结的承诺按原话那句台词对齐：
                # 这一轮还认定的保留原编号、只更新标题等字段（台词里点名过的编号不悬空，
                # 线索推送的"用上即停"仍认得它）；这一轮不再认定的才删；运营者已了结或
                # 判误记的一概不动
                delete_archive_lines_by_session(conn, live_session_id=int(live_session_id))
                kept_evidence = sorted(
                    {int(item.evidence_chat_id) for item in commitments if item.evidence_chat_id is not None}
                )
                stale_filter = ""
                if kept_evidence:
                    placeholders = ",".join("?" for _ in kept_evidence)
                    stale_filter = f" AND (evidence_chat_id IS NULL OR evidence_chat_id NOT IN ({placeholders}))"
                conn.execute(
                    "DELETE FROM commitments WHERE created_session_id=? AND status='open'" + stale_filter,  # noqa: S608 占位符由代码生成
                    (int(live_session_id), *kept_evidence),
                )
                # 上一轮整理用本场台词了结的旧承诺先放回未了结，再按这一轮的结果重新了结
                reopen_commitments_closed_by_session(
                    conn, live_session_id=int(live_session_id), updated_at_ms=int(now_ms_value)
                )
                existing_evidence: set[int] = set()
                for line in archive_lines:
                    evidence_chat_id = int(line.evidence_chat_id)
                    if evidence_chat_id in existing_evidence:
                        continue
                    conn.execute(
                        "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id) VALUES (?, ?, ?, ?)",
                        (int(live_session_id), line.text, line.quote, evidence_chat_id),
                    )
                    existing_evidence.add(evidence_chat_id)
                    written["archive"] += 1
                for item in commitments:
                    cur = conn.execute(
                        "INSERT INTO commitments(what, quote, evidence_chat_id, viewer_id, game,"
                        " created_session_id, created_at_ms, updated_at_ms)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                        # 同一句台词已登记过：本场仍未了结的那条原地更新（编号不变），
                        # 已了结、判误记或别处登记的不动
                        " ON CONFLICT(evidence_chat_id) DO UPDATE SET"
                        " what=excluded.what, quote=excluded.quote, viewer_id=excluded.viewer_id,"
                        " game=excluded.game, updated_at_ms=excluded.updated_at_ms"
                        " WHERE commitments.status='open'"
                        " AND commitments.created_session_id=excluded.created_session_id",
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
            return written

        return await self._run_in_executor(_exec)

    async def reset_settle_attempts(self, *, live_session_id: int) -> bool:
        """只把重试次数清零并清掉上次的失败原因；返回是否命中行。

        留给只清次数的调用方；要让已整理完成的场次真正重跑用 :meth:`requeue_settlement`
        （它同时清掉整理完成时刻）。失败场次本就 settled_at_ms 为空，两者效果相同。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET settle_attempts=0, settle_report=NULL WHERE id=?",
                    (int(live_session_id),),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def requeue_settlement(self, *, live_session_id: int) -> bool:
        """把一个已结束的正式场重新排进整理队列：清掉整理完成时刻与重试次数，返回是否命中行。

        运营者在 WebUI 点「重新整理」时调用：已成功整理过的场次同样要能重排（重跑时
        整场替换，不会写出重复档案行）。进行中、不存在或彩排场返回 False——彩排场
        从不整理，重排没有意义。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET settled_at_ms=NULL, settle_attempts=0, settle_report=NULL"
                    " WHERE id=? AND ended_at_ms IS NOT NULL AND kind=?",
                    (int(live_session_id), SESSION_KIND_LIVE),
                )
                return cur.rowcount > 0

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
