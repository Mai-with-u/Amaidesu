"""SessionRepo —— live_sessions 场次表仓储。

场次行由 LiveSessionManager 创建与结账：一行 = 一场直播（有开始/结束边界），
主键 AUTOINCREMENT；房间/频道是普通属性列。本仓储只提供行级领域方法，
不持有"当前场次"状态。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

from src.modules.storage.models.game_players import (
    INTERACTION_HELPED,
    INTERACTION_TOGETHER,
    PendingPlayerCandidate,
    PendingPlayerInteraction,
)
from src.modules.storage.models.social_facts import NewSocialFact
from src.modules.storage.models.memory import (
    CommitmentClosure,
    KeptSnapshotLine,
    PendingArchiveLine,
    PendingCommitment,
    PendingViewerCandidate,
)
from src.modules.storage.models.sessions import LiveSession, LiveSessionListItem
from src.modules.storage.repos._base import BaseRepo
from src.modules.storage.repos.archive import (
    delete_archive_lines_by_session,
    delete_snapshot,
    keywords_column,
    newest_snapshot_owner,
)
from src.modules.storage.repos.game_players import replace_session_player_candidates
from src.modules.storage.repos.memory_index import delete_session_index_rows
from src.modules.storage.repos.social_facts import (
    delete_social_facts_by_session,
    insert_social_fact_row,
    reopen_social_facts_superseded_by_session,
)
from src.modules.storage.repos.viewer_candidates import CANDIDATE_EXPIRE_MS, replace_session_candidates
from src.modules.storage.repos.commitments import (
    delete_commitments_by_session,
    reopen_commitments_closed_by_session,
)
from src.modules.types.session_kind import SESSION_KIND_LIVE, SESSION_KIND_REHEARSAL, SessionKind


# 下播整理的自动重试上限：用满后不再自动补跑，等运营者在 WebUI 点「重新整理」把次数清零。
# 定在存储层：整理执行器与 WebUI 的整理状态接口共用同一个数，不各写一份
MAX_SETTLE_ATTEMPTS = 3


def _approved_unseen(
    conn: sqlite3.Connection, *, live_session_id: int, seen_line_ids: Optional[Sequence[int]]
) -> List[KeptSnapshotLine]:
    """整理读原料之后才批准进最新快照的观众来源行，照它们在那份快照里的样子取出（按位置）。

    批准总是把这一行插进当时最新的那份快照；整理读原料时它还不在，``seen_line_ids`` 里就没有它。
    最新那份属于开播晚于本场的场次时，本场快照写出来也不是最新的，那份快照照旧留着这些行，不用补。
    不传 ``seen_line_ids`` 的调用方不读原料，不补。
    """
    if seen_line_ids is None:
        return []
    owner = newest_snapshot_owner(conn)
    if owner is None or not owner[1]:
        return []
    started = {
        int(row[0]): int(row[1] or 0)
        for row in conn.execute(
            "SELECT id, started_at_ms FROM live_sessions WHERE id IN (?, ?)", (int(owner[0]), int(live_session_id))
        ).fetchall()
    }
    if started.get(int(owner[0]), 0) > started.get(int(live_session_id), 0):
        return []
    seen = {int(item) for item in seen_line_ids}
    rows = conn.execute(
        "SELECT p.archive_line_id, p.lasting, p.seen_at_ms, p.pinned FROM archive_snapshot_lines p"
        " JOIN archive_lines a ON a.id = p.archive_line_id"
        " WHERE p.live_session_id=? AND a.source='viewer' ORDER BY p.position",
        (int(owner[0]),),
    ).fetchall()
    return [
        KeptSnapshotLine(
            archive_line_id=int(row[0]), lasting=bool(row[1]), seen_at_ms=int(row[2] or 0), pinned=bool(row[3])
        )
        for row in rows
        if int(row[0]) not in seen
    ]


def _write_snapshot(
    conn: sqlite3.Connection,
    *,
    live_session_id: int,
    written_at_ms: int,
    new_lines: Sequence[tuple[int, bool]],
    kept: Sequence[KeptSnapshotLine],
    front: Sequence[KeptSnapshotLine] = (),
) -> int:
    """在整理事务里写本场的快照：先登记"写过快照"（快照可以是空的），再按顺序写各行引用。

    ``front`` 排在最前（整理调模型期间人批准的观众来源行，和批准时一样插在最前面），其后是本场新写的行，
    最后是接住的旧行。本场新写的行最近一次提起就是本场（记本场开播时刻）；接住的行只在原行还在时
    才引用，运营者放回的标记跟着接进新快照（否则下一场整理就把人放回的行当普通行挤掉）。
    返回写进快照的行数。
    """
    conn.execute(
        "INSERT INTO archive_snapshots(live_session_id, written_at_ms) VALUES (?, ?)",
        (int(live_session_id), int(written_at_ms)),
    )
    started = conn.execute("SELECT started_at_ms FROM live_sessions WHERE id=?", (int(live_session_id),)).fetchone()
    started_at_ms = int(started[0] or 0) if started else 0
    position = 0
    # 批准 -> 插在最前：整理调模型期间人批准的行先占位置，和批准当时的样子一样
    for item in front:
        cur = conn.execute(
            "INSERT OR IGNORE INTO archive_snapshot_lines(live_session_id, archive_line_id, position, lasting,"
            " seen_at_ms, pinned) SELECT ?, id, ?, ?, ?, ? FROM archive_lines WHERE id=?",
            (
                int(live_session_id),
                position,
                1 if item.lasting else 0,
                int(item.seen_at_ms),
                1 if item.pinned else 0,
                int(item.archive_line_id),
            ),
        )
        position += max(0, int(cur.rowcount or 0))
    for archive_line_id, lasting in new_lines:
        conn.execute(
            "INSERT INTO archive_snapshot_lines(live_session_id, archive_line_id, position, lasting, seen_at_ms)"
            " VALUES (?, ?, ?, ?, ?)",
            (int(live_session_id), int(archive_line_id), position, 1 if lasting else 0, started_at_ms),
        )
        position += 1
    for item in kept:
        cur = conn.execute(
            "INSERT OR IGNORE INTO archive_snapshot_lines(live_session_id, archive_line_id, position, lasting,"
            " seen_at_ms, pinned) SELECT ?, id, ?, ?, ?, ? FROM archive_lines WHERE id=?",
            (
                int(live_session_id),
                position,
                1 if item.lasting else 0,
                int(item.seen_at_ms),
                1 if item.pinned else 0,
                int(item.archive_line_id),
            ),
        )
        position += max(0, int(cur.rowcount or 0))
    return position


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
                    # 本场写下的快照与档案行一起撤掉：档案行删掉后，后面几场快照里对它的引用由外键
                    # 级联撤掉，彩排里的事不会留在正式的账上
                    delete_snapshot(conn, live_session_id=int(live_session_id))
                    # 被本场社交事实取代的旧行先恢复现行：删掉本场行之后取代关系就认不出了
                    reopened_facts = reopen_social_facts_superseded_by_session(
                        conn, live_session_id=int(live_session_id), updated_at_ms=int(updated_at_ms)
                    )
                    result["ledger_removed"] = (
                        delete_archive_lines_by_session(conn, live_session_id=int(live_session_id))
                        + delete_commitments_by_session(conn, live_session_id=int(live_session_id))
                        + delete_social_facts_by_session(conn, live_session_id=int(live_session_id))
                    )
                    # 彩排台词做出的了结也作废：被它了结的旧承诺放回未了结
                    result["reopened"] = (
                        reopen_commitments_closed_by_session(
                            conn, live_session_id=int(live_session_id), updated_at_ms=int(updated_at_ms)
                        )
                        + reopened_facts
                    )
                    conn.execute(
                        "UPDATE live_sessions SET settled_at_ms=NULL, settle_attempts=0, settle_report=NULL WHERE id=?",
                        (int(live_session_id),),
                    )
                    # 彩排的话观众没听过：本场台词与游戏事件的检索索引一起删（来源行不变，触发器管不到）
                    delete_session_index_rows(conn, live_session_id=int(live_session_id))
                    # 本场的观众候选也撤掉：台词行还在，外键级联管不到；批准出的承诺已随本场承诺删掉
                    conn.execute("DELETE FROM viewer_candidates WHERE live_session_id=?", (int(live_session_id),))
                    # 彩排里和游戏玩家的互动、玩家的待审自述同样不进正式的记忆；人还认得，公屏聊天是原始记录照旧留着
                    for table in ("game_player_interactions", "player_candidates"):
                        conn.execute(f"DELETE FROM {table} WHERE live_session_id=?", (int(live_session_id),))  # noqa: S608 常量表名
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
        timeline_summary / archive_lines（均以 ``live_session_id`` 引用场次主键）与本场写下的
        档案快照；后面几场快照里对本场档案行的引用由外键级联撤掉。
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
                delete_snapshot(conn, live_session_id=int(live_session_id))
                cur = conn.execute("DELETE FROM live_sessions WHERE id=?", (live_session_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def rejected_candidate_keys(self) -> Tuple[List[int], List[str]]:
        """驳回过的观众候选挂的弹幕与标题：整理时同一条弹幕或同样标题的不再列。"""

        def _exec() -> Tuple[List[int], List[str]]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT viewer_chat_id, title FROM viewer_candidates WHERE status='rejected'"
                ).fetchall()
            return [int(row[0]) for row in rows if row[0] is not None], [str(row[1]) for row in rows]

        return await self._run_in_executor(_exec)

    async def rejected_claim_keys(self) -> Tuple[List[int], List[str]]:
        """驳回过的"她说欠着的"候选挂的那句台词与标题：整理时同一句或同样标题的不再列。"""

        def _exec() -> Tuple[List[int], List[str]]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT reply_chat_id, title FROM viewer_candidates"
                    " WHERE status='rejected' AND kind='claimed_missing'"
                ).fetchall()
            return [int(row[0]) for row in rows if row[0] is not None], [str(row[1]) for row in rows]

        return await self._run_in_executor(_exec)

    async def expire_viewer_candidates(self, *, now_ms_value: int) -> int:
        """删掉列出超过 14 天还没人审的观众候选（整理调度顺手做），返回删了几条。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "DELETE FROM viewer_candidates WHERE status='pending' AND created_at_ms < ?",
                    (int(now_ms_value) - CANDIDATE_EXPIRE_MS,),
                )
            return int(cur.rowcount or 0)

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
        snapshot_kept: Sequence[KeptSnapshotLine] = (),
        viewer_candidates: Sequence[PendingViewerCandidate] = (),
        seen_line_ids: Optional[Sequence[int]] = None,
        player_candidates: Sequence[PendingPlayerCandidate] = (),
        social_facts: Sequence[NewSocialFact] = (),
        player_interactions: Sequence[PendingPlayerInteraction] = (),
    ) -> Dict[str, int]:
        """把一场的整理结果一次写进去：档案行、档案快照、新承诺、了结、观众候选、玩家自述、整理完成时刻。

        整理必须整场原子提交：任何一步失败都回滚，不能让这一场留下半份结果。
        跨三张表的写操作只有这一处，事务边界放在场次仓储里，与删除场次的级联同一层。

        幂等：先用「settled_at_ms 为空且仍是正式场」抢占本场——同一场被两次整理同时
        挑中时只有一次写进去（begin immediate 把第二个写事务挡在对方提交之后，重读即
        看到已完成）；整理途中场次被改成彩排时，这里也写不进去。运营者重置进度后重跑
        时，同一事务里先删掉本场旧档案行（人批准的观众来源行留着）、把上一轮用本场台词了结的旧承诺
        放回未了结，再写，重跑整场替换：不会越堆越多，上一轮认定、这一轮不再认定的了结也不会留在
        账上。本场登记的未了结承诺按原话那句台词对齐——这一轮还认定的保留原编号、原地
        更新，不再认定的才删；运营者已了结或判误记的承诺不受影响。

        快照同一事务写：本场新写的行里 ``in_snapshot`` 的在前，``snapshot_kept`` 接住的旧行在后，
        按给定顺序编位置。接住的行只记引用；整理途中被运营者删掉的行就不再引用。重跑时本场旧
        快照整份替换，所以本场此前做出的去掉、替换随之作废，基底仍是上一份快照。整理调模型
        这段时间里人批准的观众候选插进了当时最新的那份快照，整理读原料时没看到（不在
        ``seen_line_ids`` 里）：同一事务里在删旧快照之前把它们读出来，写在本场快照最前面。
        游戏社交事实（下播整理第四调的归纳、补漏与修正）同一事务写：与现行行同类同句的不重复写，取代按
        存储层约束（运营者锁定的不动）。一起做的事、帮过忙两类互动只由整理写，重跑时本场这两类整份替换。
        返回各类写入条数（claimed = 1 表示这次真的抢到并写了）。
        """

        def _exec() -> Dict[str, int]:
            written = {
                "archive": 0,
                "snapshot": 0,
                "commitments": 0,
                "closed": 0,
                "claimed": 0,
                "candidates": 0,
                "player_candidates": 0,
                "social_facts": 0,
                "interactions": 0,
            }
            with self._manager.transaction(immediate=True) as conn:
                claim = conn.execute(
                    "UPDATE live_sessions SET settled_at_ms=?, settle_report=?"
                    " WHERE id=? AND settled_at_ms IS NULL AND kind=?",
                    (int(settled_at_ms), settle_report, int(live_session_id), SESSION_KIND_LIVE),
                )
                if claim.rowcount <= 0:
                    return written
                written["claimed"] = 1
                # 读原料 -> 调模型 -> 落库这段时间里人批准的观众候选：批准时插进了当时最新的那份快照，
                # 本场快照写出来成了最新的一份，不补上它们就从她的简报里消失。删旧快照前先读出来
                approved_late = _approved_unseen(
                    conn, live_session_id=int(live_session_id), seen_line_ids=seen_line_ids
                )
                # 重跑整场替换：旧档案行全清。本场登记、仍未了结的承诺按原话那句台词对齐：
                # 这一轮还认定的保留原编号、只更新标题等字段（台词里点名过的编号不悬空，
                # 线索推送的"用上即停"仍认得它）；这一轮不再认定的才删；运营者已了结或
                # 判误记的一概不动
                delete_snapshot(conn, live_session_id=int(live_session_id))
                # 人批准的观众来源行不删：它们不是整理写的，整理器把它们接着带进重写的快照
                conn.execute(
                    "DELETE FROM archive_lines WHERE live_session_id=? AND source <> 'viewer'", (int(live_session_id),)
                )
                kept_evidence = sorted(
                    {int(item.evidence_chat_id) for item in commitments if item.evidence_chat_id is not None}
                )
                # 对玩家的承诺证据也可以是她打在游戏公屏上的那句：两种证据各自对齐
                kept_game_evidence = sorted(
                    {int(item.evidence_game_chat_id) for item in commitments if item.evidence_game_chat_id is not None}
                )
                keep_clauses: List[str] = []
                if kept_evidence:
                    keep_clauses.append(f"COALESCE(evidence_chat_id IN ({','.join('?' for _ in kept_evidence)}), 0)")
                if kept_game_evidence:
                    keep_clauses.append(
                        f"COALESCE(evidence_game_chat_id IN ({','.join('?' for _ in kept_game_evidence)}), 0)"
                    )
                stale_filter = f" AND NOT ({' OR '.join(keep_clauses)})" if keep_clauses else ""
                conn.execute(
                    "DELETE FROM commitments WHERE created_session_id=? AND status='open'" + stale_filter,  # noqa: S608 占位符由代码生成
                    (int(live_session_id), *kept_evidence, *kept_game_evidence),
                )
                # 上一轮整理用本场台词了结的旧承诺先放回未了结，再按这一轮的结果重新了结
                reopen_commitments_closed_by_session(
                    conn, live_session_id=int(live_session_id), updated_at_ms=int(now_ms_value)
                )
                # 留下来的观众来源行占着它们那句台词：模型这一轮又用同一句写了一行的不再写，免得一件事两行
                existing_evidence: set[int] = {
                    int(row[0])
                    for row in conn.execute(
                        "SELECT evidence_chat_id FROM archive_lines WHERE live_session_id=?", (int(live_session_id),)
                    ).fetchall()
                }
                # (档案行主键, 是否稳定的事)：本场新写、要进快照的行
                snapshot_new: List[tuple[int, bool]] = []
                for line in archive_lines:
                    evidence_chat_id = int(line.evidence_chat_id)
                    if evidence_chat_id in existing_evidence:
                        continue
                    cur = conn.execute(
                        "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id,"
                        " game, save_id, save_name, keywords, place) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            int(live_session_id),
                            line.text,
                            line.quote,
                            evidence_chat_id,
                            line.game,
                            line.save_id,
                            line.save_name,
                            keywords_column(line.keywords),
                            line.place,
                        ),
                    )
                    existing_evidence.add(evidence_chat_id)
                    written["archive"] += 1
                    if line.in_snapshot:
                        snapshot_new.append((int(cur.lastrowid or 0), bool(line.lasting)))
                # 观众候选进待审区：本场待审的整份替换，已批、已驳回的不动（批准前对她不可见）
                written["candidates"] = replace_session_candidates(
                    conn,
                    live_session_id=int(live_session_id),
                    candidates=viewer_candidates,
                    created_at_ms=int(settled_at_ms),
                )
                # 玩家自述同样进待审区：本场待审的整份替换，批准前对她不可见
                written["player_candidates"] = replace_session_player_candidates(
                    conn,
                    live_session_id=int(live_session_id),
                    candidates=player_candidates,
                    created_at_ms=int(settled_at_ms),
                )
                written["snapshot"] = _write_snapshot(
                    conn,
                    live_session_id=int(live_session_id),
                    written_at_ms=int(settled_at_ms),
                    new_lines=snapshot_new,
                    kept=snapshot_kept,
                    front=approved_late,
                )
                for item in commitments:
                    # 证据是公屏那句的按公屏行对齐，其余按台词行对齐
                    conflict_key = (
                        "evidence_game_chat_id" if item.evidence_game_chat_id is not None else "evidence_chat_id"
                    )
                    cur = conn.execute(
                        "INSERT INTO commitments(what, quote, evidence_chat_id, evidence_game_chat_id, viewer_id,"
                        " target_player_id, game, save_id, save_name, created_session_id, created_at_ms, updated_at_ms)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                        # 同一句已登记过：本场仍未了结的那条原地更新（编号不变），
                        # 已了结、判误记或别处登记的不动
                        f" ON CONFLICT({conflict_key}) DO UPDATE SET"  # noqa: S608 冲突键取自两个代码内常量
                        " what=excluded.what, quote=excluded.quote, viewer_id=excluded.viewer_id,"
                        " target_player_id=excluded.target_player_id,"
                        " game=excluded.game, save_id=excluded.save_id, save_name=excluded.save_name,"
                        " updated_at_ms=excluded.updated_at_ms"
                        " WHERE commitments.status='open'"
                        " AND commitments.created_session_id=excluded.created_session_id",
                        (
                            item.what,
                            item.quote,
                            item.evidence_chat_id,
                            item.evidence_game_chat_id,
                            item.viewer_id,
                            item.target_player_id,
                            item.game,
                            item.save_id,
                            item.save_name,
                            int(live_session_id),
                            int(now_ms_value),
                            int(now_ms_value),
                        ),
                    )
                    written["commitments"] += max(0, int(cur.rowcount or 0))
                for fact in social_facts:
                    if insert_social_fact_row(conn, fact=fact, now_ms_value=int(now_ms_value), skip_duplicate=True):
                        written["social_facts"] += 1
                # 一起做的事与帮过忙只由整理写：重跑时本场这两类整份替换
                conn.execute(
                    "DELETE FROM game_player_interactions WHERE live_session_id=? AND kind IN (?, ?)",
                    (int(live_session_id), INTERACTION_TOGETHER, INTERACTION_HELPED),
                )
                for interaction in player_interactions:
                    conn.execute(
                        "INSERT INTO game_player_interactions(player_id, live_session_id, save_id, save_name, kind,"
                        " fact, at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            int(interaction.player_id),
                            int(live_session_id),
                            interaction.save_id,
                            interaction.save_name,
                            interaction.kind,
                            json.dumps(interaction.fact, ensure_ascii=False),
                            int(interaction.at_ms),
                        ),
                    )
                    written["interactions"] += 1
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
