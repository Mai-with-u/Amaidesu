"""数据保留期清理器：默认不自动删除，按使用者配置的保留天数清理到期工作数据并执行宽限删除。

清理范围：
- 到期的"忘了我"宽限删除请求（始终检查，不受保留期为 0 影响）
- ``raw_days > 0`` 时（从场次结束时刻算起）：
  1. 删除 ``live_chat`` 到期的观众行（保留主播台词行；人批准进记忆的观众候选挂着的那条弹幕也保留，
     删了它档案行会随外键级联消失——保留期只清工作数据，不清记忆）；公屏游戏聊天同一口径，删到期场次里
     玩家说的与认不出是谁的占位行，她自己在公屏说的留着
  2. 清空付费三表（``gifts`` / ``super_chats`` / ``guards``）到期行的观众身份、昵称、留言与原始消息，保留金额、类型、时刻
  3. 删除到期场次的话题与时间线摘要（``topics`` / ``timeline_summary``），以及委派账（``game_events`` 里
     ``delegation_*`` 类型的行：她的原话与终态摘要，只给下播核对与续播认领用）
  4. 清空 ``llm_requests`` 到期行的响应正文与预览（保留 token/耗时/费用统计列）
  5. 删除 ``<db目录>/backups/`` 下最后修改时间早于截止时刻的整库备份文件与迁移前移入的
     旧记忆目录（``memory-pre-v*``）：迁移前快照与普通备份同一口径，都受使用者配置的保留期管理
- ``viewer_absence_days > 0`` 时：对最后活跃早于截止时刻的观众调用统一删除函数（不加入不记录名单）
- 有数据库行被删除或擦除时，执行 ``PRAGMA wal_checkpoint(TRUNCATE)`` 截断预写日志残留。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from src.modules.logging import get_logger
from src.modules.storage.connection import SQLiteConnectionManager
from src.modules.storage.privacy import PrivacyService
from src.modules.storage.repos.events import DELEGATION_EVENT_PREFIX
from src.modules.storage.run_in_executor import run_in_executor
from src.modules.utils.time_utils import now_ms

logger = get_logger("RetentionService")

_DAY_MS: int = 86_400_000


@dataclass(frozen=True)
class RetentionResult:
    """单次保留期与宽限删除清理统计。"""

    forget_deleted: int = 0
    chat_deleted: int = 0
    payments_scrubbed: int = 0
    summaries_deleted: int = 0
    delegations_deleted: int = 0
    llm_bodies_scrubbed: int = 0
    backups_deleted: int = 0
    absent_viewers_deleted: int = 0


class RetentionService:
    """保留期与到期遗忘删除统一调度器。"""

    def __init__(
        self,
        manager: SQLiteConnectionManager,
        privacy: PrivacyService,
        *,
        db_path: Path,
        raw_days: int = 0,
        viewer_absence_days: int = 0,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._manager = manager
        self._privacy = privacy
        self._db_path = Path(db_path)
        self._raw_days = max(0, int(raw_days))
        self._viewer_absence_days = max(0, int(viewer_absence_days))
        self._clock = clock

    @property
    def raw_days(self) -> int:
        return self._raw_days

    @property
    def viewer_absence_days(self) -> int:
        return self._viewer_absence_days

    async def run_cleanup(
        self,
        *,
        now_ms: Optional[int] = None,
        after_stream_end: bool = False,
    ) -> RetentionResult:
        """执行到期宽限删除与配置的保留期清理。"""
        current = int(now_ms) if now_ms is not None else int(self._clock())

        forget_deleted = await self._privacy.execute_due_forget_deletions(
            now_ms=current,
            force_after_stream_end=after_stream_end,
        )

        if self._raw_days <= 0 and self._viewer_absence_days <= 0:
            return RetentionResult(forget_deleted=forget_deleted)

        chat_del = pay_scrub = sum_del = deleg_del = llm_scrub = backups_del = 0
        if self._raw_days > 0:
            raw_cutoff = current - self._raw_days * _DAY_MS
            chat_del, pay_scrub, sum_del, deleg_del, llm_scrub, backups_del = await run_in_executor(
                self._cleanup_raw_blocking, raw_cutoff
            )

        absent_del = 0
        if self._viewer_absence_days > 0:
            absence_cutoff = current - self._viewer_absence_days * _DAY_MS
            stale_ids = await run_in_executor(self._list_absent_viewer_ids_blocking, absence_cutoff)
            for vid in stale_ids:
                if await self._privacy.delete_viewer(viewer_id=vid, add_to_forget_list=False, at_ms=current):
                    absent_del += 1

        result = RetentionResult(
            forget_deleted=forget_deleted,
            chat_deleted=chat_del,
            payments_scrubbed=pay_scrub,
            summaries_deleted=sum_del,
            delegations_deleted=deleg_del,
            llm_bodies_scrubbed=llm_scrub,
            backups_deleted=backups_del,
            absent_viewers_deleted=absent_del,
        )
        if any((chat_del, pay_scrub, sum_del, deleg_del, llm_scrub, backups_del, absent_del)):
            logger.info(
                f"保留期清理完成: 弹幕={chat_del}, 付费脱敏={pay_scrub}, 话题摘要={sum_del}, 委派账={deleg_del}, "
                f"请求正文={llm_scrub}, 备份={backups_del}, 缺席观众={absent_del}"
            )
        return result

    def _cleanup_raw_blocking(self, cutoff_ms: int) -> tuple[int, int, int, int, int, int]:
        chat_del = pay_scrub = sum_del = deleg_del = llm_scrub = 0
        expired_sessions_sql = "SELECT id FROM live_sessions WHERE ended_at_ms IS NOT NULL AND ended_at_ms <= ?"
        orphan_session_sql = "SELECT id FROM live_sessions"

        with self._manager.transaction(immediate=True) as conn:
            cur = conn.execute(
                f"DELETE FROM live_chat WHERE sender_role = 'viewer'"  # noqa: S608 常量 SQL 片段
                f" AND (live_session_id IN ({expired_sessions_sql})"
                f"      OR (live_session_id NOT IN ({orphan_session_sql}) AND timestamp_ms <= ?))"
                # 批准进记忆的观众候选挂着的弹幕留着：删了它，那一行档案会被外键级联删掉
                " AND id NOT IN (SELECT viewer_chat_id FROM archive_lines WHERE viewer_chat_id IS NOT NULL)",
                (cutoff_ms, cutoff_ms),
            )
            chat_del += max(0, cur.rowcount)

            # 公屏游戏聊天和弹幕一样是工作数据：到期场次里玩家说的、认不出是谁的占位行删掉，她说的留着
            cur = conn.execute(
                f"DELETE FROM game_chat WHERE direction <> 'streamer'"  # noqa: S608 常量 SQL 片段
                f" AND (live_session_id IN ({expired_sessions_sql})"
                f"      OR (live_session_id NOT IN ({orphan_session_sql}) AND at_ms <= ?))"
                # 批准进玩家卡的自述挂着的他那句留着：删了它，那条自述会被外键级联删掉
                " AND id NOT IN (SELECT player_chat_id FROM player_candidates WHERE status = 'approved')",
                (cutoff_ms, cutoff_ms),
            )
            chat_del += max(0, cur.rowcount)

            cur = conn.execute(
                f"UPDATE gifts SET viewer_id = NULL, user_id = '', user_name = '', id_kind = '',"  # noqa: S608
                f" fans_medal_name = '', raw_data = NULL"
                f" WHERE (live_session_id IN ({expired_sessions_sql})"
                f"        OR (live_session_id NOT IN ({orphan_session_sql}) AND timestamp_ms <= ?))"
                f"   AND (viewer_id IS NOT NULL OR user_id <> '' OR user_name <> '' OR raw_data IS NOT NULL)",
                (cutoff_ms, cutoff_ms),
            )
            pay_scrub += max(0, cur.rowcount)

            cur = conn.execute(
                f"UPDATE super_chats SET viewer_id = NULL, user_id = '', user_name = '', id_kind = '',"  # noqa: S608
                f" message = '', fans_medal_name = '', raw_data = NULL"
                f" WHERE (live_session_id IN ({expired_sessions_sql})"
                f"        OR (live_session_id NOT IN ({orphan_session_sql}) AND timestamp_ms <= ?))"
                f"   AND (viewer_id IS NOT NULL OR user_id <> '' OR user_name <> ''"
                f"        OR message <> '' OR raw_data IS NOT NULL)",
                (cutoff_ms, cutoff_ms),
            )
            pay_scrub += max(0, cur.rowcount)

            cur = conn.execute(
                f"UPDATE guards SET viewer_id = NULL, user_id = '', user_name = '', id_kind = '',"  # noqa: S608
                f" fans_medal_name = '', raw_data = NULL"
                f" WHERE (live_session_id IN ({expired_sessions_sql})"
                f"        OR (live_session_id NOT IN ({orphan_session_sql}) AND timestamp_ms <= ?))"
                f"   AND (viewer_id IS NOT NULL OR user_id <> '' OR user_name <> '' OR raw_data IS NOT NULL)",
                (cutoff_ms, cutoff_ms),
            )
            pay_scrub += max(0, cur.rowcount)

            cur = conn.execute(
                f"DELETE FROM topics WHERE live_session_id IN ({expired_sessions_sql})",  # noqa: S608
                (cutoff_ms,),
            )
            sum_del += max(0, cur.rowcount)

            cur = conn.execute(
                f"DELETE FROM timeline_summary"  # noqa: S608
                f" WHERE live_session_id IN ({expired_sessions_sql}) OR end_ms <= ?",
                (cutoff_ms, cutoff_ms),
            )
            sum_del += max(0, cur.rowcount)

            # 委派账是工作数据：下播整理核对完、续播认领过就不再要，到期场次的随话题一起删
            cur = conn.execute(
                f"DELETE FROM game_events WHERE substr(event_type, 1, {len(DELEGATION_EVENT_PREFIX)}) = ?"  # noqa: S608
                f" AND (live_session_id IN ({expired_sessions_sql})"
                f"      OR (live_session_id NOT IN ({orphan_session_sql}) AND timestamp_ms <= ?))",
                (DELEGATION_EVENT_PREFIX, cutoff_ms, cutoff_ms),
            )
            deleg_del += max(0, cur.rowcount)

            cur = conn.execute(
                "UPDATE llm_requests SET response_content = NULL, preview_text = ''"
                " WHERE timestamp_ms <= ? AND (response_content IS NOT NULL OR preview_text <> '')",
                (cutoff_ms,),
            )
            llm_scrub += max(0, cur.rowcount)

        if any((chat_del, pay_scrub, sum_del, deleg_del, llm_scrub)):
            conn = self._manager.connection()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        backups_del = self._delete_expired_backups(cutoff_ms)

        return chat_del, pay_scrub, sum_del, deleg_del, llm_scrub, backups_del

    def _delete_expired_backups(self, cutoff_ms: int) -> int:
        """删除 backups/ 下到期的备份条目，返回删除数。

        参与清理的有两类，判定口径一致：迁移前整库快照（``*-pre-v*.db``，与普通
        ``*.db`` 备份同样按自身修改时刻）与迁移时移入的旧记忆目录
        （``memory-pre-v*``）。目录按**目录自身的修改时刻**判定：目录 mtime 只在其中
        增删改条目名时变化，目录里文件内容被改写不会改变它——这里判的是"整份备份有多旧"，
        这个口径即可，不跟随内容变化。保留期交给使用者：``raw_days = 0`` 时本方法不会被
        调用，备份永不自动删除；删不掉的条目记日志、不中断其余清理。
        """
        backup_dir = self._db_path.parent / "backups"
        if not backup_dir.is_dir():
            return 0
        deleted = 0
        for item in backup_dir.iterdir():
            is_db_backup = item.is_file() and item.suffix == ".db"
            is_memory_backup = item.is_dir() and item.name.startswith("memory-pre-v")
            if not (is_db_backup or is_memory_backup):
                continue
            try:
                if int(item.stat().st_mtime * 1000) > cutoff_ms:
                    continue
                if is_memory_backup:
                    shutil.rmtree(item)
                else:
                    item.unlink(missing_ok=True)
                deleted += 1
            except OSError as exc:  # noqa: BLE001 单个备份删除失败记日志不中断
                logger.warning(f"删除过期备份失败（{item}）: {exc}", exc=exc)
        return deleted

    def _list_absent_viewer_ids_blocking(self, cutoff_ms: int) -> List[int]:
        with self._manager.transaction() as conn:
            rows = conn.execute(
                "SELECT id FROM viewers WHERE last_active_ms <= ? ORDER BY id ASC",
                (cutoff_ms,),
            ).fetchall()
            return [int(r["id"]) for r in rows]


__all__ = ["RetentionResult", "RetentionService"]
