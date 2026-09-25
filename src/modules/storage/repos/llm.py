"""LLMRepo —— LLM 调用记录仓储（llm_usage + llm_requests）。

- ``llm_usage``：每次 LLM 调用的 token/费用记录（observation 写入）。
- ``llm_requests``：完整请求/响应历史（observation 写入）。
  usage 拆平为列以便 SQL 聚合（statistics/费用汇总）；request_params 与
  tool_calls 结构不定，存 JSON 文本。dashboard 历史页按时间倒序分页查询。
- 两表经 ``request_id`` 连接；``insert_llm_call`` 把两表写入放进同一事务，
  供调用方做"聚合账 + 请求明细"的原子记账。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.modules.storage.repos._base import BaseRepo
from src.modules.time_utils import now_ms


@dataclass
class LLMUsageInsert:
    """``llm_usage`` 单行插入载荷（时间戳 None 表示入库时取当前毫秒）。"""

    model_name: str
    provider_name: str
    request_type: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0
    duration_ms: int = 0
    profile_name: Optional[str] = None
    assign_name: Optional[str] = None
    live_session_id: Optional[int] = None
    request_id: Optional[str] = None
    timestamp_ms: Optional[int] = None


@dataclass
class LLMRequestInsert:
    """``llm_requests`` 单行插入载荷（JSON 列由调用方序列化好传入）。

    ``profile_name`` 替换历史 ``client_type``（§2 扩容正名）：该列实际承载的
    是 LLM 用途 profile 名，与 ``LLMProviderConfig.client_type``（客户端实现
    标识）概念不同。旧值域 ``llm/llm_fast/llm_summary/vlm`` 原样保留以备
    历史查询（不映射），新行一律为 profile 闭集合成员。
    """

    request_id: str
    timestamp_ms: int
    profile_name: str = ""
    model_name: str = ""
    request_params_json: Optional[str] = None
    response_content: Optional[str] = None
    reasoning_content: Optional[str] = None
    tool_calls_json: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0
    success: bool = True
    error: Optional[str] = None
    latency_ms: int = 0
    usage_raw_json: Optional[str] = None


class LLMRepo(BaseRepo):
    """llm_usage / llm_requests 两张表的读写。"""

    @staticmethod
    def _usage_row_params(row: LLMUsageInsert) -> tuple:
        """组装 llm_usage 插入参数（时间戳在入库时刻解析）。"""
        ts = row.timestamp_ms if row.timestamp_ms is not None else now_ms()
        return (
            row.live_session_id,
            row.model_name,
            row.assign_name,
            row.profile_name,
            row.provider_name,
            row.request_type,
            row.prompt_tokens,
            row.completion_tokens,
            row.total_tokens,
            row.cache_hit_tokens,
            row.cache_miss_tokens,
            row.reasoning_tokens,
            row.cost,
            row.duration_ms,
            ts,
            row.request_id,
        )

    @staticmethod
    def _usage_insert_sql() -> str:
        return (
            "INSERT INTO llm_usage ("
            "live_session_id, model_name, assign_name, profile_name, provider_name,"
            " request_type, prompt_tokens, completion_tokens, total_tokens,"
            " cache_hit_tokens, cache_miss_tokens, reasoning_tokens, cost, duration_ms,"
            " timestamp_ms, request_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )

    @staticmethod
    def _request_row_params(row: LLMRequestInsert) -> tuple:
        """组装 llm_requests 插入参数。"""
        return (
            row.request_id,
            row.timestamp_ms,
            row.profile_name,
            row.model_name,
            row.request_params_json,
            row.response_content,
            row.reasoning_content,
            row.tool_calls_json,
            row.prompt_tokens,
            row.completion_tokens,
            row.total_tokens,
            row.cache_hit_tokens,
            row.cache_miss_tokens,
            row.reasoning_tokens,
            row.cost,
            1 if row.success else 0,
            row.error,
            row.latency_ms,
            row.usage_raw_json,
        )

    @staticmethod
    def _request_insert_sql() -> str:
        return (
            "INSERT OR IGNORE INTO llm_requests ("
            "request_id, timestamp_ms, profile_name, model_name, request_params, response_content,"
            " reasoning_content, tool_calls, prompt_tokens, completion_tokens, total_tokens,"
            " cache_hit_tokens, cache_miss_tokens, reasoning_tokens, cost, success, error,"
            " latency_ms, usage_raw_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )

    async def insert_llm_usage_row(self, row: LLMUsageInsert) -> int:
        """按插入载荷写一条 ``llm_usage``，返回 lastrowid（dataclass 直插面）。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(self._usage_insert_sql(), self._usage_row_params(row))
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def insert_llm_usage(self, **kwargs: Any) -> int:
        """插入一条 ``llm_usage`` 调用记录，返回 lastrowid。"""
        return await self.insert_llm_usage_row(LLMUsageInsert(**kwargs))

    async def insert_llm_request(
        self,
        *,
        request_id: str,
        timestamp_ms: int,
        profile_name: str = "",
        model_name: str = "",
        request_params_json: Optional[str] = None,
        response_content: Optional[str] = None,
        reasoning_content: Optional[str] = None,
        tool_calls_json: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
        latency_ms: int = 0,
        usage_raw_json: Optional[str] = None,
    ) -> bool:
        """插入一条请求历史行；``request_id`` 冲突时忽略（幂等）。

        Returns:
            True 实际插入；False 已存在被忽略。
        """
        row = LLMRequestInsert(
            request_id=request_id,
            timestamp_ms=timestamp_ms,
            profile_name=profile_name,
            model_name=model_name,
            request_params_json=request_params_json,
            response_content=response_content,
            reasoning_content=reasoning_content,
            tool_calls_json=tool_calls_json,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            reasoning_tokens=reasoning_tokens,
            cost=cost,
            success=success,
            error=error,
            latency_ms=latency_ms,
            usage_raw_json=usage_raw_json,
        )

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(self._request_insert_sql(), self._request_row_params(row))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def insert_llm_call(
        self,
        *,
        usage: LLMUsageInsert,
        request: LLMRequestInsert,
    ) -> int:
        """同事务写入 ``llm_usage`` 与 ``llm_requests`` 两行，返回 usage 行 rowid。

        连接键由调用方保证：两载荷填同一个 ``request_id`` 时聚合账与请求
        明细可 join。第二步失败（如约束/触发器报错）整体回滚，两表均无新行。
        """

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(self._usage_insert_sql(), self._usage_row_params(usage))
                usage_rowid = int(cur.lastrowid or 0)
                conn.execute(self._request_insert_sql(), self._request_row_params(request))
                return usage_rowid

        return await self._run_in_executor(_exec)

    @staticmethod
    def _llm_request_where(
        *,
        profile_name: Optional[str],
        model_name: Optional[str],
        start_time: Optional[int],
        end_time: Optional[int],
        success_only: Optional[bool],
    ) -> "tuple[str, List[Any]]":
        """组装 llm_requests 查询的 WHERE 子句（子句全部为代码内常量）。"""
        clauses: List[str] = []
        params: List[Any] = []
        if profile_name:
            clauses.append("profile_name = ?")
            params.append(profile_name)
        if model_name:
            clauses.append("model_name = ?")
            params.append(model_name)
        if start_time is not None:
            clauses.append("timestamp_ms >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("timestamp_ms <= ?")
            params.append(end_time)
        if success_only is not None:
            clauses.append("success = ?")
            params.append(1 if success_only else 0)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    async def query_llm_requests(
        self,
        *,
        profile_name: Optional[str] = None,
        model_name: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        success_only: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """按条件分页查询请求历史（时间倒序），返回 ``{"total", "rows"}``（原始 dict 行）。"""
        where, params = self._llm_request_where(
            profile_name=profile_name,
            model_name=model_name,
            start_time=start_time,
            end_time=end_time,
            success_only=success_only,
        )

        def _exec() -> Dict[str, Any]:
            with self._manager.transaction() as conn:
                total = int(
                    conn.execute(f"SELECT COUNT(*) AS n FROM llm_requests{where}", tuple(params)).fetchone()["n"]  # noqa: S608 子句为代码内常量
                )
                offset = max(0, (page - 1) * page_size)
                rows = conn.execute(
                    f"SELECT * FROM llm_requests{where} ORDER BY timestamp_ms DESC LIMIT ? OFFSET ?",  # noqa: S608 子句为代码内常量
                    (*params, page_size, offset),
                ).fetchall()
                return {"total": total, "rows": [dict(row) for row in rows]}

        return await self._run_in_executor(_exec)

    async def get_llm_request_by_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        """按 request_id 取单条请求历史，未命中返回 None。"""
        rows = await self._execute("SELECT * FROM llm_requests WHERE request_id = ?", (request_id,))
        return dict(rows[0]) if rows else None

    async def delete_llm_requests_before(self, *, before_date: Optional[str] = None) -> int:
        """删除请求历史；``before_date``（本地日 YYYY-MM-DD）为 None 时清空全部，返回删除行数。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                if before_date is None:
                    cur = conn.execute("DELETE FROM llm_requests")
                else:
                    cutoff_ms = int(datetime.strptime(before_date, "%Y-%m-%d").timestamp() * 1000)
                    cur = conn.execute("DELETE FROM llm_requests WHERE timestamp_ms < ?", (cutoff_ms,))
                return int(cur.rowcount or 0)

        return await self._run_in_executor(_exec)

    async def llm_request_available_dates(self) -> List[str]:
        """列出有请求历史记录的本地日期（降序）。"""
        rows = await self._execute(
            "SELECT DISTINCT date(timestamp_ms / 1000, 'unixepoch', 'localtime') AS d FROM llm_requests ORDER BY d DESC"
        )
        return [str(row["d"]) for row in rows if row["d"] is not None]

    async def llm_request_models(self) -> List[str]:
        """列出请求历史中出现过的模型名（去重升序；空名不返回）。"""
        rows = await self._execute(
            "SELECT DISTINCT model_name FROM llm_requests WHERE model_name != '' ORDER BY model_name"
        )
        return [str(row["model_name"]) for row in rows]

    async def llm_request_statistics(
        self,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> Dict[str, Any]:
        """聚合请求历史统计：总体指标 + 按模型 + 按 profile_name（一次方法三次查询）。"""
        where, params = self._llm_request_where(
            profile_name=None,
            model_name=None,
            start_time=start_time,
            end_time=end_time,
            success_only=None,
        )

        def _exec() -> Dict[str, Any]:
            with self._manager.transaction() as conn:
                overall = conn.execute(
                    "SELECT COUNT(*) AS total,"
                    " COALESCE(SUM(success), 0) AS success_count,"
                    " COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,"
                    " COALESCE(SUM(completion_tokens), 0) AS completion_tokens,"
                    " COALESCE(SUM(total_tokens), 0) AS total_tokens,"
                    " COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,"
                    " COALESCE(SUM(cost), 0) AS total_cost,"
                    " COALESCE(AVG(latency_ms), 0) AS avg_latency,"
                    " COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,"
                    " COALESCE(SUM(cache_miss_tokens), 0) AS cache_miss_tokens"
                    f" FROM llm_requests{where}",  # noqa: S608 子句为代码内常量
                    tuple(params),
                ).fetchone()
                model_rows = conn.execute(
                    "SELECT model_name, COUNT(*) AS count,"
                    " COALESCE(SUM(total_tokens), 0) AS total_tokens,"
                    " COALESCE(SUM(cost), 0) AS total_cost,"
                    " COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,"
                    " COALESCE(SUM(cache_miss_tokens), 0) AS cache_miss_tokens"
                    f" FROM llm_requests{where} GROUP BY model_name",  # noqa: S608 子句为代码内常量
                    tuple(params),
                ).fetchall()
                client_rows = conn.execute(
                    f"SELECT profile_name, COUNT(*) AS count FROM llm_requests{where} GROUP BY profile_name",  # noqa: S608 子句为代码内常量
                    tuple(params),
                ).fetchall()
                return {
                    "overall": dict(overall) if overall else {},
                    "by_model": [dict(row) for row in model_rows],
                    "by_client": [dict(row) for row in client_rows],
                }

        return await self._run_in_executor(_exec)

    async def llm_usage_by_model(self) -> List[Dict[str, Any]]:
        """按模型聚合 ``llm_usage`` 全量用量（dashboard /usage 数据源）。

        每行含 prompt/completion/total token 与费用/调用次数聚合、cache 两列
        聚合，以及首次/最后调用时间与最后更新时间（毫秒时间戳，无记录时为 None）。
        """
        rows = await self._execute(
            "SELECT model_name,"
            " COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens,"
            " COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens,"
            " COALESCE(SUM(total_tokens), 0) AS total_tokens,"
            " COUNT(*) AS total_calls,"
            " COALESCE(SUM(cost), 0) AS total_cost,"
            " COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,"
            " COALESCE(SUM(cache_miss_tokens), 0) AS cache_miss_tokens,"
            " COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,"
            " MIN(timestamp_ms) AS first_call_time,"
            " MAX(timestamp_ms) AS last_call_time,"
            " MAX(timestamp_ms) AS last_updated"
            " FROM llm_usage GROUP BY model_name ORDER BY total_cost DESC"
        )
        return [dict(row) for row in rows]

    async def llm_usage_latest_prompt_tokens(self) -> Dict[str, int]:
        """每模型最近一次调用的 prompt_tokens（按 ``timestamp_ms`` 取最大值那行）。

        返回 ``{model_name: prompt_tokens}``；无记录时不返回键。
        用于 Dashboard 模型用量详情"上下文水位"分子（与 context_window 配对比率）。
        """
        rows = await self._execute(
            "SELECT model_name, prompt_tokens FROM llm_usage t1"
            " WHERE timestamp_ms = ("
            " SELECT MAX(timestamp_ms) FROM llm_usage t2 WHERE t2.model_name = t1.model_name"
            " )"
        )
        result: Dict[str, int] = {}
        for row in rows:
            name = str(row["model_name"] or "")
            if not name:
                continue
            result[name] = int(row["prompt_tokens"] or 0)
        return result

    async def llm_usage_daily_trends(self, *, start_ms: int) -> Dict[str, Any]:
        """按本地日聚合 ``llm_usage`` 用量趋势（图表数据源）。

        返回 ``{"daily": [...], "by_model": [...]}``：daily 为整体逐日聚合
        （日期字符串 day + 调用/token/cache/费用），by_model 在 daily 维度上
        多一列 model_name。无数据的日期不产出行，由调用方补零对齐时间轴。
        """

        def _exec() -> Dict[str, Any]:
            with self._manager.transaction() as conn:
                daily = conn.execute(
                    "SELECT date(timestamp_ms / 1000, 'unixepoch', 'localtime') AS day,"
                    " COUNT(*) AS total_calls,"
                    " COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,"
                    " COALESCE(SUM(completion_tokens), 0) AS completion_tokens,"
                    " COALESCE(SUM(total_tokens), 0) AS total_tokens,"
                    " COALESCE(SUM(cost), 0) AS cost,"
                    " COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,"
                    " COALESCE(SUM(cache_miss_tokens), 0) AS cache_miss_tokens,"
                    " COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens"
                    " FROM llm_usage WHERE timestamp_ms >= ?"
                    " GROUP BY day ORDER BY day",
                    (start_ms,),
                ).fetchall()
                by_model = conn.execute(
                    "SELECT date(timestamp_ms / 1000, 'unixepoch', 'localtime') AS day,"
                    " model_name, COUNT(*) AS total_calls,"
                    " COALESCE(SUM(total_tokens), 0) AS total_tokens,"
                    " COALESCE(SUM(cost), 0) AS cost,"
                    " COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,"
                    " COALESCE(SUM(cache_miss_tokens), 0) AS cache_miss_tokens,"
                    " COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens"
                    " FROM llm_usage WHERE timestamp_ms >= ?"
                    " GROUP BY day, model_name ORDER BY day",
                    (start_ms,),
                ).fetchall()
                return {
                    "daily": [dict(row) for row in daily],
                    "by_model": [dict(row) for row in by_model],
                }

        return await self._run_in_executor(_exec)

    async def llm_usage_summary(self) -> Dict[str, Any]:
        """聚合 ``llm_usage`` 全量摘要（dashboard /usage/summary 数据源）。"""

        def _exec() -> Dict[str, Any]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(cost), 0) AS total_cost,"
                    " COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens,"
                    " COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens,"
                    " COALESCE(SUM(total_tokens), 0) AS total_tokens,"
                    " COUNT(*) AS total_calls,"
                    " COUNT(DISTINCT model_name) AS model_count,"
                    " COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,"
                    " COALESCE(SUM(cache_miss_tokens), 0) AS cache_miss_tokens,"
                    " COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens"
                    " FROM llm_usage"
                ).fetchone()
                return dict(row) if row else {}

        return await self._run_in_executor(_exec)

    async def _execute(self, sql: str, params: Any = ()) -> List[sqlite3.Row]:
        """仓储内单条 SQL 执行（SELECT 为主），返回 Row 列表。"""

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cursor = conn.execute(sql, params if params is not None else ())
                return list(cursor.fetchall())

        return await self._run_in_executor(_exec)


__all__ = ["LLMRepo"]
