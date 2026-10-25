"""ViewerRepo —— viewers 观众统计表仓储。

跨场客观数字（发言/送礼/被回复/互动/付费计数）。记账器对每条观众消息先
``resolve_viewer`` 把外部身份归到一位观众（``viewers.id`` 即内部观众 ID），
再按观众 ID 累加计数；dashboard 读取排行与观众档案。

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
from src.modules.utils.time_utils import now_ms


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

    async def resolve_viewer(self, *, platform: str, external_id: str, user_name: str, timestamp_ms: int) -> int:
        """把消息的外部身份归到一位观众，返回内部观众 ID：已建档则更新昵称，没建档则新建（计数全 0）。"""

        def _exec() -> int:
            # 先查后建必须拿写锁再查：记账器并发处理同一位新观众的两条消息时，
            # 后到的一条要等先到的建档提交后再查，才不会重复新建、撞唯一约束丢统计
            with self._manager.transaction(immediate=True) as conn:
                row = conn.execute(
                    "SELECT id, user_name FROM viewers WHERE platform=? AND user_id=?", (platform, external_id)
                ).fetchone()
                if row is not None:
                    if row["user_name"] != user_name:
                        conn.execute("UPDATE viewers SET user_name=? WHERE id=?", (user_name, int(row["id"])))
                    return int(row["id"])
                cur = conn.execute(
                    "INSERT INTO viewers(platform, user_id, user_name, last_active_ms) VALUES (?, ?, ?, ?)",
                    (platform, external_id, user_name, timestamp_ms),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def find_viewer_id(self, *, platform: str, external_id: str) -> Optional[int]:
        """按外部 ID 查观众 ID；只查不建，未命中返回 None。"""

        def _exec() -> Optional[int]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT id FROM viewers WHERE platform=? AND user_id=?", (platform, external_id)
                ).fetchone()
                return int(row["id"]) if row else None

        return await self._run_in_executor(_exec)

    async def record_message(self, *, viewer_id: int, timestamp_ms: int) -> None:
        """观众发言：``message_count`` +1，``interaction_count`` +1。"""
        await self._bump(
            viewer_id, timestamp_ms, "message_count=message_count+1, interaction_count=interaction_count+1"
        )

    async def record_gift(self, *, viewer_id: int, timestamp_ms: int) -> None:
        """观众送礼：``gift_count`` +1，``interaction_count`` +1（付费金额另走 ``record_paid``）。"""
        await self._bump(viewer_id, timestamp_ms, "gift_count=gift_count+1, interaction_count=interaction_count+1")

    async def record_paid(self, *, viewer_id: int, amount: int, timestamp_ms: int) -> None:
        """观众付费：``paid_count`` +1，``paid_amount`` +amount（金瓜子）。

        付费即互动，但只在这是该观众的第一笔互动时记 1：礼物的互动已由
        ``record_gift`` 记过，避免一次事件计两次。调用方须先按 ``currency``
        过滤——免费礼物（银瓜子/空币种）不应进入本方法。
        """
        await self._bump(
            viewer_id,
            timestamp_ms,
            "paid_count=paid_count+1, paid_amount=paid_amount+?,"
            " interaction_count=interaction_count+(CASE WHEN interaction_count=0 THEN 1 ELSE 0 END)",
            (int(amount),),
        )

    async def record_replied(self, *, viewer_id: int, timestamp_ms: int) -> None:
        """主播回复了该观众：``replied_count`` +1，``interaction_count`` +1。"""
        await self._bump(
            viewer_id, timestamp_ms, "replied_count=replied_count+1, interaction_count=interaction_count+1"
        )

    async def _bump(self, viewer_id: int, timestamp_ms: int, set_sql: str, params: Tuple[Any, ...] = ()) -> None:
        """按观众 ID 累加计数并刷新最近活跃时刻（``set_sql`` 只来自本类常量）。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    f"UPDATE viewers SET {set_sql}, last_active_ms=? WHERE id=?",  # noqa: S608 常量片段
                    (*params, timestamp_ms, viewer_id),
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
