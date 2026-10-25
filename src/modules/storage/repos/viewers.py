"""ViewerRepo —— viewers 观众表仓储。

一行 = 某个平台上的一位观众，``viewers.id`` 是内部观众 ID。外部身份记在同一行：
``account_id``（平台账号 ID，B 站 uid）与 ``open_id`` + ``open_app_id``（开放平台
发给某个应用的 ID）。记账器对每条观众消息先 ``resolve_viewer`` 归到一位观众，
再按观众 ID 记计数。

**解析规则**（同一平台内）：

1. 同种 ID 命中就是这个人；每次命中刷新 ``name_seen_ms``，昵称变了则改名并
   记入昵称记录（``viewer_names``）。
2. ID 未命中时，在同平台、同 ``simulated`` 的观众里找当前昵称完全相同、且对应
   ID 列空着的候选。对应列已有另一个值的同名观众不是候选——同一种 ID 不同值
   就是另一个账号，说明昵称已经易主。
3. 没有候选就新建；唯一候选且它的昵称在时限内出现过，就把新 ID 记到它身上；
   超出时限或候选不止一个，则新建并标为待确认，建议并入昵称最近出现的候选。
4. 占位昵称（采集器拿不到昵称时填的值、打码昵称）不参与匹配，也不覆盖已知昵称。

只按当前昵称匹配，曾用名不参与：旧昵称可能已经被别人拿走。

计数口径为**历史累计**：场次删除会级联清理明细，但本表计数不随之回滚，
WebUI 以"历史累计 / 现存记录"口径标注这一差异。付费统计（``paid_count`` /
``paid_amount``）：付费次数 = 礼物 + SC + 上舰，金额单位 = 平台最小虚拟货币
单位（B 站金瓜子）；免费礼物（银瓜子）由调用方按 ``currency`` 过滤后不计入。

按外部 ID 查询的读接口（``get_viewer_stats`` / ``list_viewer_stats`` /
``get_viewer_id_by_name``）给只拿得到消息里外部 ID 的调用方用：返回行带
``user_id`` 列 = 该观众的代表外部 ID（账号 ID 优先，其次开放平台 ID，没有
真实 ID 时为空串）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Literal, Optional, Tuple

from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms

IdKind = Literal["account", "open"]
ResolveOutcome = Literal["matched_id", "linked_by_name", "created", "pending"]

# 采集器拿不到昵称时填的占位名：官方开放平台填 unknown，LAPLACE 与旧版填"未知用户"，
# 早期主播回复尚未建档的观众时记过"?"；这些名字不代表任何人
_PLACEHOLDER_NAMES = frozenset({"", "?", "unknown", "未知用户"})
# 未登录连接拿到的昵称会被打码成"用***"一类，同样不能拿来认人
_MASK_MARK = "***"

# 代表外部 ID：账号 ID 优先，其次开放平台 ID
_USER_ID_EXPR = "COALESCE(account_id, open_id, '')"
_SELECT_VIEWER = f"SELECT *, {_USER_ID_EXPR} AS user_id FROM viewers"
_MATCH_EXTERNAL = "platform=? AND (account_id=? OR open_id=?)"


def is_placeholder_name(name: Optional[str]) -> bool:
    """昵称是否为占位值（不参与昵称匹配、不覆盖已知昵称、不进昵称记录）。"""
    text = (name or "").strip()
    return text.lower() in _PLACEHOLDER_NAMES or _MASK_MARK in text


@dataclass(frozen=True)
class ViewerResolution:
    """一次身份解析的结果：归到哪位观众，以及是怎么归过去的。"""

    viewer_id: int
    outcome: ResolveOutcome
    # outcome=pending 时为建议并入的老观众；linked_by_name 时为被关联的观众（同 viewer_id）
    candidate_id: Optional[int] = None


class ViewerRepo(BaseRepo):
    """viewers 观众表与 viewer_names 昵称记录的读写。"""

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

    # -------------------- 按外部 ID 查询 --------------------

    async def get_viewer_stats(self, *, platform: str, user_id: str) -> Optional[sqlite3.Row]:
        """按平台 + 外部 ID 查单行观众（带 ``user_id`` 代表外部 ID 列）；未命中返回 None。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return conn.execute(
                    f"{_SELECT_VIEWER} WHERE {_MATCH_EXTERNAL} ORDER BY id LIMIT 1",  # noqa: S608 常量片段
                    (platform, user_id, user_id),
                ).fetchone()

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
        """列出观众（支持平台过滤 / 搜索 / 分页），返回 ``(rows, total)``。

        ``platform`` 为 None 时返回全部平台；``search`` 对账号 ID / 开放平台 ID /
        昵称做 LIKE 模糊匹配（``%`` / ``_`` / ``\\`` 按字面解释，经 ESCAPE 转义）；
        ``total`` 为命中过滤条件的全量行数，供分页器使用。``order_by`` 仅接受
        白名单列名，非法值直接 ``ValueError``——避免任何列名拼接。
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
            clauses.append(
                "(account_id LIKE ? ESCAPE '\\' OR open_id LIKE ? ESCAPE '\\' OR user_name LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern, pattern])
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        def _exec() -> Tuple[List[sqlite3.Row], int]:
            with self._manager.transaction() as conn:
                total_row = conn.execute("SELECT COUNT(*) AS n FROM viewers" + where_sql, params).fetchone()
                total = int(total_row["n"]) if total_row else 0
                cur = conn.execute(
                    f"{_SELECT_VIEWER}{where_sql} ORDER BY {order_by} DESC LIMIT ? OFFSET ?",  # noqa: S608
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

    # -------------------- 身份解析 --------------------

    async def resolve_viewer(
        self,
        *,
        platform: str,
        id_kind: IdKind,
        external_id: str,
        user_name: str,
        timestamp_ms: int,
        name_match_window_ms: int,
        open_app_id: str = "",
        simulated: bool = False,
    ) -> ViewerResolution:
        """把一条消息的外部身份归到一位观众（规则见模块说明），必要时新建观众。"""

        def _exec() -> ViewerResolution:
            # 先查后建必须拿写锁再查：记账器并发处理同一位新观众的两条消息时，
            # 后到的一条要等先到的建档提交后再查，才不会重复新建、撞唯一约束丢统计
            with self._manager.transaction(immediate=True) as conn:
                # 先按 ID 找人：找到了就是他，顺带刷新昵称
                viewer_id = _find_by_id(conn, platform, id_kind, external_id, open_app_id)
                if viewer_id is not None:
                    _touch_name(conn, viewer_id, user_name, timestamp_ms)
                    return ViewerResolution(viewer_id, "matched_id")
                # ID 没见过：按当前昵称找同平台的老观众，时限内唯一候选才自动关联
                candidates = (
                    []
                    if is_placeholder_name(user_name)
                    else _name_candidates(conn, platform, id_kind, open_app_id, user_name, simulated)
                )
                if len(candidates) == 1 and timestamp_ms - int(candidates[0]["name_seen_ms"]) <= name_match_window_ms:
                    linked = int(candidates[0]["id"])
                    _attach_id(conn, linked, id_kind, external_id, open_app_id)
                    _touch_name(conn, linked, user_name, timestamp_ms)
                    return ViewerResolution(linked, "linked_by_name", linked)
                # 超出时限或候选不唯一：先建新观众照常处理消息，挂一条并入建议等人工确认
                suggested = max(candidates, key=lambda r: int(r["name_seen_ms"]))["id"] if candidates else None
                new_id = _insert_viewer(
                    conn,
                    platform=platform,
                    id_kind=id_kind,
                    external_id=external_id,
                    open_app_id=open_app_id,
                    user_name=user_name,
                    timestamp_ms=timestamp_ms,
                    simulated=simulated,
                    candidate_id=int(suggested) if suggested is not None else None,
                )
                if suggested is None:
                    return ViewerResolution(new_id, "created")
                return ViewerResolution(new_id, "pending", int(suggested))

        return await self._run_in_executor(_exec)

    async def find_viewer_id(self, *, platform: str, external_id: str) -> Optional[int]:
        """按外部 ID（账号 ID 或开放平台 ID）查观众 ID；只查不建，未命中返回 None。"""

        def _exec() -> Optional[int]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    f"SELECT id FROM viewers WHERE {_MATCH_EXTERNAL} ORDER BY id LIMIT 1",  # noqa: S608 常量片段
                    (platform, external_id, external_id),
                ).fetchone()
                return int(row["id"]) if row else None

        return await self._run_in_executor(_exec)

    # -------------------- 计数（按观众 ID） --------------------

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
        ``record_gift`` 记过，避免一次事件计两次。
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
        """按当前昵称反查代表外部 ID（最近活跃优先）；未命中或该观众没有真实 ID 返回 None。

        画像查询的"昵称找人"路径：画像表不存昵称（昵称随改名漂移，权威在
        viewers），注入/工具查询时经本方法实时解析。
        """

        def _exec() -> Optional[str]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    f"SELECT {_USER_ID_EXPR} AS user_id FROM viewers"  # noqa: S608 常量片段
                    f" WHERE platform=? AND user_name=? AND {_USER_ID_EXPR}<>''"
                    " ORDER BY last_active_ms DESC LIMIT 1",
                    (platform, user_name),
                ).fetchone()
                return str(row["user_id"]) if row else None

        return await self._run_in_executor(_exec)


# -------------------- 解析用的同步小步骤（在调用方事务内执行） --------------------


def _find_by_id(
    conn: sqlite3.Connection, platform: str, id_kind: IdKind, external_id: str, open_app_id: str
) -> Optional[int]:
    """同平台按同种 ID 找观众。

    开放平台 ID 优先找同一应用发的；存量迁移来的行不知道应用（应用 ID 为空），
    也算命中，并就地补上当前应用。
    """
    if id_kind == "account":
        row = conn.execute(
            "SELECT id FROM viewers WHERE platform=? AND account_id=?", (platform, external_id)
        ).fetchone()
        return int(row["id"]) if row else None
    row = conn.execute(
        "SELECT id, open_app_id FROM viewers WHERE platform=? AND open_id=? AND (open_app_id=? OR open_app_id='')"
        " ORDER BY (open_app_id=?) DESC LIMIT 1",
        (platform, external_id, open_app_id, open_app_id),
    ).fetchone()
    if row is None:
        return None
    if not row["open_app_id"] and open_app_id:
        conn.execute("UPDATE viewers SET open_app_id=? WHERE id=?", (open_app_id, int(row["id"])))
    return int(row["id"])


def _name_candidates(
    conn: sqlite3.Connection,
    platform: str,
    id_kind: IdKind,
    open_app_id: str,
    user_name: str,
    simulated: bool,
) -> List[sqlite3.Row]:
    """同平台、同真假、当前昵称相同、且对应 ID 列"空着"的老观众。

    账号 ID：候选的 ``account_id`` 为空。开放平台 ID：候选的 ``open_id`` 为空，
    或它的 ``open_id`` 来自另一个已知应用（换了开放平台应用）；应用不明的存量
    ``open_id`` 无法判断是不是同一个应用，按"已有另一个值"处理。
    """
    rows = conn.execute(
        "SELECT id, name_seen_ms, account_id, open_id, open_app_id FROM viewers"
        " WHERE platform=? AND simulated=? AND user_name=?",
        (platform, 1 if simulated else 0, user_name),
    ).fetchall()
    if id_kind == "account":
        return [r for r in rows if r["account_id"] is None]
    return [r for r in rows if r["open_id"] is None or (r["open_app_id"] and r["open_app_id"] != open_app_id)]


def _attach_id(conn: sqlite3.Connection, viewer_id: int, id_kind: IdKind, external_id: str, open_app_id: str) -> None:
    """把新 ID 记到观众身上；换开放平台应用时覆盖旧的 open_id 与应用 ID。"""
    if id_kind == "account":
        conn.execute("UPDATE viewers SET account_id=? WHERE id=?", (external_id, viewer_id))
    else:
        conn.execute("UPDATE viewers SET open_id=?, open_app_id=? WHERE id=?", (external_id, open_app_id, viewer_id))


def _record_name(conn: sqlite3.Connection, viewer_id: int, user_name: str, timestamp_ms: int) -> None:
    """昵称记录：首次出现建行，改回用过的旧名沿用原行并推后最晚出现时刻。"""
    conn.execute(
        "INSERT INTO viewer_names(viewer_id, user_name, first_seen_ms, last_seen_ms) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(viewer_id, user_name) DO UPDATE SET last_seen_ms=MAX(last_seen_ms, excluded.last_seen_ms)",
        (viewer_id, user_name, timestamp_ms, timestamp_ms),
    )


def _touch_name(conn: sqlite3.Connection, viewer_id: int, user_name: str, timestamp_ms: int) -> None:
    """按 ID 认出观众后更新昵称。

    昵称没变只刷新 ``name_seen_ms``；变了就改名：旧昵称在昵称记录里的最晚出现
    时刻定为改名前的 ``name_seen_ms``，再记下新昵称。占位昵称不覆盖已知昵称。
    """
    if is_placeholder_name(user_name):
        return
    row = conn.execute("SELECT user_name, name_seen_ms FROM viewers WHERE id=?", (viewer_id,)).fetchone()
    if row is None:
        return
    if row["user_name"] == user_name:
        conn.execute("UPDATE viewers SET name_seen_ms=MAX(name_seen_ms, ?) WHERE id=?", (timestamp_ms, viewer_id))
        return
    conn.execute(
        "UPDATE viewer_names SET last_seen_ms=MAX(last_seen_ms, ?) WHERE viewer_id=? AND user_name=?",
        (int(row["name_seen_ms"]), viewer_id, row["user_name"]),
    )
    _record_name(conn, viewer_id, user_name, timestamp_ms)
    conn.execute("UPDATE viewers SET user_name=?, name_seen_ms=? WHERE id=?", (user_name, timestamp_ms, viewer_id))


def _insert_viewer(
    conn: sqlite3.Connection,
    *,
    platform: str,
    id_kind: IdKind,
    external_id: str,
    open_app_id: str,
    user_name: str,
    timestamp_ms: int,
    simulated: bool,
    candidate_id: Optional[int],
) -> int:
    """新建观众（计数全 0），有并入建议时标为待确认；返回内部观众 ID。"""
    cur = conn.execute(
        "INSERT INTO viewers(platform, account_id, open_id, open_app_id, user_name, name_seen_ms, simulated,"
        " link_status, link_candidate_id, last_active_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            platform,
            external_id if id_kind == "account" else None,
            external_id if id_kind == "open" else None,
            open_app_id if id_kind == "open" else None,
            user_name,
            timestamp_ms,
            1 if simulated else 0,
            "pending" if candidate_id is not None else "confirmed",
            candidate_id,
            timestamp_ms,
        ),
    )
    viewer_id = int(cur.lastrowid or 0)
    if not is_placeholder_name(user_name):
        _record_name(conn, viewer_id, user_name, timestamp_ms)
    return viewer_id


__all__ = ["ViewerRepo", "ViewerResolution", "is_placeholder_name"]
