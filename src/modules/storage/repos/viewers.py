"""ViewerRepo —— viewers 观众统计表仓储。

跨场客观数字（发言/送礼/被回复/互动/付费计数），由 StorageLedger 在主表
落库同点 upsert 写穿，dashboard 读取排行与观众档案。

**身份键 = ``(platform, user_id)`` 复合键**（表唯一约束）：B 站真实观众与
console 调试用户等不同平台账号视为不同的人，统计天然隔离。所有读写按
platform 参数定位行，禁止退回单平台假设。

计数口径为**历史累计**：观众是真实互动过的档案主体，其互动事实独立于
明细存续——场次删除会级联清理 live_chat 等明细行，但本表计数不随之
回滚。因此 viewers 数字大于现存明细聚合属预期状态，WebUI 侧以"历史
累计 / 现存记录"口径标注呈现这一差异。

付费统计（``paid_count`` / ``paid_amount``）：付费次数 = 礼物 + SC + 上舰；
``paid_amount`` 单位 = 平台最小虚拟货币单位（B 站金瓜子），展示层 ÷1000
= 元。**免费礼物（银瓜子）不计入**——按事件 ``currency`` 过滤后由调用方
决定是否累加。``paid_amount`` 是派生值（可从付费明细三表重算），单位将来
可调整，不构成锁死。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

from src.modules.storage.repos._base import BaseRepo
from src.modules.time_utils import now_ms


class ViewerRepo(BaseRepo):
    """viewers 观众统计表的读写。"""

    # list_viewer_stats 排序白名单——防 SQL 注入，禁止字符串拼接列名
    _ORDER_BY_WHITELIST: tuple = (
        "message_count",
        "gift_count",
        "replied_count",
        "interaction_count",
        "paid_count",
        "paid_amount",
        "last_active_ms",
    )

    async def get_viewer_stats(self, *, platform: str, user_id: str) -> Optional[sqlite3.Row]:
        """按 (platform, user_id) 查单行观众统计；未命中返回 None。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "SELECT * FROM viewers WHERE platform=? AND user_id=?",
                    (platform, user_id),
                )
                return cur.fetchone()

        return await self._run_in_executor(_exec)

    async def list_viewer_stats(
        self,
        *,
        platform: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        order_by: str = "message_count",
    ) -> Tuple[List[sqlite3.Row], int]:
        """列出观众统计（支持平台过滤 / 搜索 / 分页），返回 ``(rows, total)``。

        ``platform`` 为 None 时返回全部平台（dashboard 全局视图用）；
        指定时只返回该平台。``search`` 对 ``user_id`` / ``user_name`` 做 LIKE
        模糊匹配（``%`` / ``_`` / ``\\`` 按字面解释，经 ESCAPE 转义）；
        ``total`` 为命中过滤条件的全量行数（不受 limit/offset 影响），供
        分页器使用。

        ``order_by`` 仅接受白名单列名（``message_count``/``gift_count``/
        ``replied_count``/``interaction_count``/``paid_count``/``paid_amount``/
        ``last_active_ms``），非法值直接 ``ValueError``——避免任何列名拼接。
        """
        if order_by not in self._ORDER_BY_WHITELIST:
            raise ValueError(f"list_viewer_stats order_by 非法: {order_by!r}，允许值: {self._ORDER_BY_WHITELIST}")

        clauses: List[str] = []
        params: List[Any] = []
        if platform:
            clauses.append("platform=?")
            params.append(platform)
        if search:
            # LIKE 元字符按字面匹配：手工转义 + ESCAPE 子句，杜绝搜索框注入通配符
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.append("(user_id LIKE ? ESCAPE '\\' OR user_name LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        def _exec() -> Tuple[List[sqlite3.Row], int]:
            with self._manager.transaction() as conn:
                total_row = conn.execute("SELECT COUNT(*) AS n FROM viewers" + where_sql, params).fetchone()
                total = int(total_row["n"]) if total_row else 0
                cur = conn.execute(
                    f"SELECT * FROM viewers{where_sql} ORDER BY {order_by} DESC LIMIT ? OFFSET ?",  # noqa: S608
                    [*params, limit, offset],
                )
                return list(cur.fetchall()), total

        return await self._run_in_executor(_exec)

    async def viewer_insight_buckets(self) -> Dict[str, int]:
        """观众活跃分桶与互动覆盖计数（互动分析页数据面）。

        分桶互斥：``active_today``（本地时区今日零点后活跃）、``active_week``
        （近 7 天但非今日）、``active_month``（近 30 天但非近 7 天）、
        ``active_older``（更早）。``never_replied`` 为主播从未回复过的观众数，
        ``gift_viewers`` 为送过礼的观众数。
        """

        now = now_ms()
        today_start_ms = int(datetime.combine(datetime.now().date(), dt_time.min).timestamp() * 1000)
        week_ms = now - 7 * 86_400_000
        month_ms = now - 30 * 86_400_000

        def _exec() -> Dict[str, int]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT"
                    " COUNT(*) AS total,"
                    " COALESCE(SUM(CASE WHEN last_active_ms >= ? THEN 1 ELSE 0 END), 0) AS active_today,"
                    " COALESCE(SUM(CASE WHEN last_active_ms >= ? AND last_active_ms < ? THEN 1 ELSE 0 END), 0) AS active_week,"
                    " COALESCE(SUM(CASE WHEN last_active_ms >= ? AND last_active_ms < ? THEN 1 ELSE 0 END), 0) AS active_month,"
                    " COALESCE(SUM(CASE WHEN last_active_ms < ? THEN 1 ELSE 0 END), 0) AS active_older,"
                    " COALESCE(SUM(CASE WHEN replied_count = 0 THEN 1 ELSE 0 END), 0) AS never_replied,"
                    " COALESCE(SUM(CASE WHEN gift_count > 0 THEN 1 ELSE 0 END), 0) AS gift_viewers"
                    " FROM viewers",
                    (today_start_ms, week_ms, today_start_ms, month_ms, week_ms, month_ms),
                ).fetchone()
                return {key: int(row[key]) if row is not None else 0 for key in row.keys()} if row is not None else {}

        return await self._run_in_executor(_exec)

    async def upsert_viewer_message(self, *, platform: str, user_id: str, user_name: str, timestamp_ms: int) -> None:
        """观众发言计数：``message_count`` +1，``interaction_count`` +1。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewers("
                    "platform, user_id, user_name, message_count, gift_count, replied_count,"
                    " interaction_count, paid_count, paid_amount, last_active_ms"
                    ") VALUES (?, ?, ?, 1, 0, 0, 1, 0, 0, ?) "
                    "ON CONFLICT(platform, user_id) DO UPDATE SET "
                    "user_name=excluded.user_name, "
                    "message_count=message_count+1, "
                    "interaction_count=interaction_count+1, "
                    "last_active_ms=excluded.last_active_ms",
                    (platform, user_id, user_name, timestamp_ms),
                )

        await self._run_in_executor(_exec)

    async def upsert_viewer_gift(self, *, platform: str, user_id: str, user_name: str, timestamp_ms: int) -> None:
        """观众送礼计数：``gift_count`` +1，``interaction_count`` +1。

        只更新互动与件数维度，付费金额走 ``upsert_viewer_paid``（免费礼物
        计互动不计付费，两路分开由调用方编排）。
        """

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewers("
                    "platform, user_id, user_name, message_count, gift_count, replied_count,"
                    " interaction_count, paid_count, paid_amount, last_active_ms"
                    ") VALUES (?, ?, ?, 0, 1, 0, 1, 0, 0, ?) "
                    "ON CONFLICT(platform, user_id) DO UPDATE SET "
                    "user_name=excluded.user_name, "
                    "gift_count=gift_count+1, "
                    "interaction_count=interaction_count+1, "
                    "last_active_ms=excluded.last_active_ms",
                    (platform, user_id, user_name, timestamp_ms),
                )

        await self._run_in_executor(_exec)

    async def upsert_viewer_paid(
        self,
        *,
        platform: str,
        user_id: str,
        user_name: str,
        amount: int,
        timestamp_ms: int,
    ) -> None:
        """观众付费计数：``paid_count`` +1，``paid_amount`` +amount。

        ``amount`` 单位 = 平台最小虚拟货币单位（B 站金瓜子）；调用方须先行
        按 ``currency`` 过滤——免费礼物（银瓜子/空币种）不应进入本方法。
        SC 观众由此进入 viewers 统计（此前 SC 只写明细表、统计空白）。
        互动计数只在新首建行时记 1（付费即互动）；行已存在时不重复累加——
        礼物的互动维度由 ``upsert_viewer_gift`` 承担，避免一次事件计两次。
        """

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewers("
                    "platform, user_id, user_name, message_count, gift_count, replied_count,"
                    " interaction_count, paid_count, paid_amount, last_active_ms"
                    ") VALUES (?, ?, ?, 0, 0, 0, 1, 1, ?, ?) "
                    "ON CONFLICT(platform, user_id) DO UPDATE SET "
                    "user_name=excluded.user_name, "
                    "paid_count=paid_count+1, "
                    "paid_amount=paid_amount+excluded.paid_amount, "
                    "last_active_ms=excluded.last_active_ms",
                    (platform, user_id, user_name, amount, timestamp_ms),
                )

        await self._run_in_executor(_exec)

    async def upsert_viewer_replied(self, *, platform: str, user_id: str, timestamp_ms: int) -> None:
        """主播回复该用户计数：``replied_count`` +1，``interaction_count`` +1。

        若该 (platform, user_id) 首次进入统计行（未发过言也未送过礼），以
        "?" 占位 user_name 首建——不强制外部补传用户名（用户名的权威来源
        是发言/礼物事件）。
        """

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewers("
                    "platform, user_id, user_name, message_count, gift_count, replied_count,"
                    " interaction_count, paid_count, paid_amount, last_active_ms"
                    ") VALUES (?, ?, '?', 0, 0, 1, 1, 0, 0, ?) "
                    "ON CONFLICT(platform, user_id) DO UPDATE SET "
                    "replied_count=replied_count+1, "
                    "interaction_count=interaction_count+1, "
                    "last_active_ms=excluded.last_active_ms",
                    (platform, user_id, timestamp_ms),
                )

        await self._run_in_executor(_exec)

    async def get_viewer_id_by_name(self, *, platform: str, user_name: str) -> Optional[str]:
        """按昵称反查 user_id（最近活跃优先）；未命中返回 None。

        画像查询的"昵称找人"路径：画像表不存昵称（昵称随改名漂移，
        权威在 viewers），注入/工具查询时经本方法实时解析。
        """

        def _exec() -> Optional[str]:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "SELECT user_id FROM viewers WHERE platform=? AND user_name=? ORDER BY last_active_ms DESC LIMIT 1",
                    (platform, user_name),
                )
                row = cur.fetchone()
                return str(row["user_id"]) if row else None

        return await self._run_in_executor(_exec)


__all__ = ["ViewerRepo"]
