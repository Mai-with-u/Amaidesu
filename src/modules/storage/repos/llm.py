"""LLMRepo —— LLM 调用记录仓储（llm_usage + llm_requests）。

- ``llm_usage``：每次 LLM 调用的 token/费用记录（LLMManager 写入）。
- ``llm_requests``：完整请求/响应历史（RequestHistoryManager 落库）。
  usage 拆平为三列以便 SQL 聚合（statistics/费用汇总）；request_params 与
  tool_calls 结构不定，存 JSON 文本。dashboard 历史页按时间倒序分页查询。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.modules.storage.repos._base import BaseRepo
from src.modules.time_utils import now_ms


class LLMRepo(BaseRepo):
    """llm_usage / llm_requests 两张表的读写。"""

    async def insert_llm_usage(
        self,
        *,
        model_name: str,
        provider_name: str,
        request_type: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
        cost: float = 0.0,
        duration_ms: int = 0,
        profile_name: Optional[str] = None,
        assign_name: Optional[str] = None,
        live_session_id: Optional[int] = None,
        timestamp_ms: Optional[int] = None,
    ) -> int:
        """插入一条 ``llm_usage`` 调用记录，返回 lastrowid。"""
        ts = timestamp_ms if timestamp_ms is not None else now_ms()

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO llm_usage ("
                    "live_session_id, model_name, assign_name, profile_name, provider_name,"
                    " request_type, prompt_tokens, completion_tokens, total_tokens,"
                    " cache_hit_tokens, cache_miss_tokens, cost, duration_ms, timestamp_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        live_session_id,
                        model_name,
                        assign_name,
                        profile_name,
                        provider_name,
                        request_type,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                        cache_hit_tokens,
                        cache_miss_tokens,
                        cost,
                        duration_ms,
                        ts,
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def insert_llm_request(
        self,
        *,
        request_id: str,
        timestamp_ms: int,
        client_type: str = "",
        model_name: str = "",
        request_params_json: Optional[str] = None,
        response_content: Optional[str] = None,
        reasoning_content: Optional[str] = None,
        tool_calls_json: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
        latency_ms: int = 0,
    ) -> bool:
        """插入一条请求历史行；``request_id`` 冲突时忽略（幂等）。

        Returns:
            True 实际插入；False 已存在被忽略。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO llm_requests ("
                    "request_id, timestamp_ms, client_type, model_name, request_params, response_content,"
                    " reasoning_content, tool_calls, prompt_tokens, completion_tokens, total_tokens,"
                    " cost, success, error, latency_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        request_id,
                        timestamp_ms,
                        client_type,
                        model_name,
                        request_params_json,
                        response_content,
                        reasoning_content,
                        tool_calls_json,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                        cost,
                        1 if success else 0,
                        error,
                        latency_ms,
                    ),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    @staticmethod
    def _llm_request_where(
        *,
        client_type: Optional[str],
        model_name: Optional[str],
        start_time: Optional[int],
        end_time: Optional[int],
        success_only: Optional[bool],
    ) -> "tuple[str, List[Any]]":
        """组装 llm_requests 查询的 WHERE 子句（子句全部为代码内常量）。"""
        clauses: List[str] = []
        params: List[Any] = []
        if client_type:
            clauses.append("client_type = ?")
            params.append(client_type)
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
        client_type: Optional[str] = None,
        model_name: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        success_only: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """按条件分页查询请求历史（时间倒序），返回 ``{"total", "rows"}``（原始 dict 行）。"""
        where, params = self._llm_request_where(
            client_type=client_type,
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

    async def llm_request_statistics(
        self,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> Dict[str, Any]:
        """聚合请求历史统计：总体指标 + 按模型 + 按客户端类型（一次方法三次查询）。"""
        where, params = self._llm_request_where(
            client_type=None,
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
                    " COALESCE(SUM(cost), 0) AS total_cost,"
                    " COALESCE(AVG(latency_ms), 0) AS avg_latency"
                    f" FROM llm_requests{where}",  # noqa: S608 子句为代码内常量
                    tuple(params),
                ).fetchone()
                model_rows = conn.execute(
                    "SELECT model_name, COUNT(*) AS count,"
                    " COALESCE(SUM(total_tokens), 0) AS total_tokens,"
                    " COALESCE(SUM(cost), 0) AS total_cost"
                    f" FROM llm_requests{where} GROUP BY model_name",  # noqa: S608 子句为代码内常量
                    tuple(params),
                ).fetchall()
                client_rows = conn.execute(
                    f"SELECT client_type, COUNT(*) AS count FROM llm_requests{where} GROUP BY client_type",  # noqa: S608 子句为代码内常量
                    tuple(params),
                ).fetchall()
                return {
                    "overall": dict(overall) if overall else {},
                    "by_model": [dict(row) for row in model_rows],
                    "by_client": [dict(row) for row in client_rows],
                }

        return await self._run_in_executor(_exec)

    async def _execute(self, sql: str, params: Any = ()) -> List[sqlite3.Row]:
        """仓储内单条 SQL 执行（SELECT 为主），返回 Row 列表。"""

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cursor = conn.execute(sql, params if params is not None else ())
                return list(cursor.fetchall())

        return await self._run_in_executor(_exec)


__all__ = ["LLMRepo"]
