"""LLM 请求历史读面（SQLite ``llm_requests`` 表之上的查询接口）

写入侧已收敛到 :mod:`src.modules.llm.observation` 的出账单一入口（引擎在
每次调用结束时一次落账）；本类只承担 Dashboard 的查询面：把仓储查询结果
映射为历史记录字典，查询签名与返回形状对下游保持稳定。

生产装配由组合根注入 ``LLMRepo``（与引擎写侧同库同源）；未注入时查询
返回空结果（仅装配退化场景，不提供替代的查询语义）。

实例经构造器注入持有方（DashboardServer 查询），组合根构造一份共享实例
传入，不做全局单例。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.modules.llm.observation import build_prompt_preview, count_image_parts  # noqa: F401  再导出：Dashboard（api/llm.py）与预览测试从此模块引用这两个写侧助手
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.storage.repos import LLMRepo


class RequestHistoryManager:
    """LLM 请求历史查询管理器（``llm_requests`` 表的读侧唯一入口）

    支持分页、筛选、统计与清理；全部转发到 ``LLMRepo`` 的 SQL 查询，
    本层只做行 → 历史记录字典的形状映射。
    """

    def __init__(self, llm_repo: Optional["LLMRepo"] = None) -> None:
        """初始化请求历史查询管理器

        Args:
            llm_repo: 查询目标（LLMRepo）；None 时查询一律返回空结果
        """
        self.logger = get_logger("RequestHistoryManager")
        self._llm_repo = llm_repo

    def _repo(self) -> Optional["LLMRepo"]:
        """取当前注入的查询目标；未注入时告警一次并返回 None。"""
        if self._llm_repo is None:
            self.logger.warning("请求历史管理器未接入存储，查询返回空结果（请检查组合根装配）")
        return self._llm_repo

    # -------------------- 查询 --------------------

    @staticmethod
    def _row_to_record(row: Dict[str, Any]) -> Dict[str, Any]:
        """DB 行 → 历史记录字典（usage 嵌套还原）。

        读取 ``profile_name`` 列（v12 迁移后该列已改名），回带 reasoning_tokens
        与 preview_text（列表预览摘要）、image_count（注入截图计数）。正文
        与分段占用已出库到诊断文件，详情读侧经 ``read_llm_request_payload``
        回查，不在本层还原。
        """
        usage = None
        if row.get("prompt_tokens") or row.get("completion_tokens") or row.get("total_tokens"):
            usage = {
                "prompt_tokens": int(row.get("prompt_tokens", 0)),
                "completion_tokens": int(row.get("completion_tokens", 0)),
                "total_tokens": int(row.get("total_tokens", 0)),
                "reasoning_tokens": (int(row["reasoning_tokens"]) if row.get("reasoning_tokens") is not None else None),
            }

        return {
            "request_id": row.get("request_id", ""),
            "timestamp": row.get("timestamp_ms", 0),
            "profile_name": row.get("profile_name", ""),
            "model_name": row.get("model_name", ""),
            "response_content": row.get("response_content"),
            "usage": usage,
            "cost": float(row.get("cost", 0.0)),
            "success": bool(row.get("success", 1)),
            "error": row.get("error"),
            "latency_ms": int(row.get("latency_ms", 0)),
            "cache_hit_tokens": int(row.get("cache_hit_tokens") or 0),
            "cache_miss_tokens": int(row.get("cache_miss_tokens") or 0),
            "preview_text": row.get("preview_text") or "",
            "image_count": int(row.get("image_count") or 0),
        }

    async def get_request_by_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取单个请求详情

        Args:
            request_id: 请求 ID

        Returns:
            请求记录字典，未找到返回 None
        """
        repo = self._repo()
        if repo is None:
            return None
        row = await repo.get_llm_request_by_id(request_id)
        return self._row_to_record(row) if row is not None else None

    async def get_request_stats_by_ids(self, request_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """按 id 批量取请求统计（仅统计列，不含正文大字段）。

        观察面统计胶囊合批懒取的数据面：返回 ``id → 统计字典``，未命中的
        id 不在结果中（前端按缺渲染不取数）。
        """
        unique = [rid for rid in dict.fromkeys(request_ids) if rid]
        if not unique:
            return {}
        repo = self._repo()
        if repo is None:
            return {}
        rows = await repo.get_llm_request_stats(unique)
        return {
            str(row.get("request_id")): {
                "model_name": str(row.get("model_name") or ""),
                "prompt_tokens": int(row.get("prompt_tokens") or 0),
                "completion_tokens": int(row.get("completion_tokens") or 0),
                "cache_hit_tokens": int(row.get("cache_hit_tokens") or 0),
                "cache_miss_tokens": int(row.get("cache_miss_tokens") or 0),
            }
            for row in rows
        }

    async def get_history(
        self,
        profile_name: Optional[str] = None,
        model_name: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        success_only: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """获取历史列表（时间倒序分页）

        Args:
            profile_name: LLM 用途 profile 名筛选
            model_name: 模型名称筛选
            start_time: 开始时间（毫秒时间戳）
            end_time: 结束时间（毫秒时间戳）
            success_only: 是否只返回成功的请求
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            包含 records, total, page, page_size, total_pages 的字典
        """
        empty = {"records": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}
        repo = self._repo()
        if repo is None:
            return empty
        result = await repo.query_llm_requests(
            profile_name=profile_name,
            model_name=model_name,
            start_time=start_time,
            end_time=end_time,
            success_only=success_only,
            page=page,
            page_size=page_size,
        )
        records = [self._row_to_record(row) for row in result["rows"]]
        total = int(result["total"])
        return {
            "records": records,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
        }

    async def clear_history(
        self,
        before_date: Optional[str] = None,
        confirm: bool = False,
    ) -> Dict[str, Any]:
        """清除历史记录

        Args:
            before_date: 清除此日期之前的记录（格式：YYYY-MM-DD），为 None 则清除所有
            confirm: 确认清除，必须为 True 才会执行

        Returns:
            操作结果字典
        """
        if not confirm:
            return {
                "success": False,
                "message": "必须设置 confirm=True 才能执行清除操作",
            }

        repo = self._repo()
        if repo is None:
            return {
                "success": False,
                "message": "未接入持久化存储，无历史可清除",
            }

        try:
            deleted = await repo.delete_llm_requests_before(before_date=before_date)
            self.logger.info(f"已清除 {deleted} 条请求历史记录")
            return {
                "success": True,
                "cleared_records": deleted,
                "message": f"成功清除 {deleted} 条记录",
            }
        except Exception as e:  # noqa: BLE001 边界处吸收 + 日志
            self.logger.error(f"清除历史记录失败: {e}", exc=True)
            return {
                "success": False,
                "message": f"清除失败: {e}",
            }

    async def get_statistics(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> Dict[str, Any]:
        """获取统计信息

        Args:
            start_time: 开始时间（毫秒时间戳）
            end_time: 结束时间（毫秒时间戳）

        Returns:
            统计信息字典
        """
        time_range = {"start": start_time, "end": end_time}
        repo = self._repo()
        if repo is None:
            return {
                "total_requests": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "success_rate": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
                "avg_latency_ms": 0.0,
                "model_stats": {},
                "client_stats": {},
                "time_range": time_range,
            }

        stats = await repo.llm_request_statistics(start_time=start_time, end_time=end_time)
        overall = stats["overall"]
        total_requests = int(overall.get("total", 0))
        successful_requests = int(overall.get("success_count", 0))

        model_stats: Dict[str, Dict[str, Any]] = {}
        for row in stats["by_model"]:
            model_stats[row.get("model_name") or "unknown"] = {
                "count": int(row.get("count", 0)),
                "total_tokens": int(row.get("total_tokens", 0)),
                "total_cost": float(row.get("total_cost", 0.0)),
                "cache_hit_tokens": int(row.get("cache_hit_tokens", 0)),
                "cache_miss_tokens": int(row.get("cache_miss_tokens", 0)),
            }

        client_stats: Dict[str, int] = {
            (row.get("profile_name") or "unknown"): int(row.get("count", 0)) for row in stats["by_client"]
        }

        return {
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": total_requests - successful_requests,
            "success_rate": successful_requests / total_requests if total_requests > 0 else 0,
            "total_prompt_tokens": int(overall.get("prompt_tokens", 0)),
            "total_completion_tokens": int(overall.get("completion_tokens", 0)),
            "total_tokens": int(overall.get("total_tokens", 0)),
            "total_cost": float(overall.get("total_cost", 0.0)),
            "cache_hit_tokens": int(overall.get("cache_hit_tokens", 0)),
            "cache_miss_tokens": int(overall.get("cache_miss_tokens", 0)),
            "avg_latency_ms": float(overall.get("avg_latency", 0.0)),
            "model_stats": model_stats,
            "client_stats": client_stats,
            "time_range": time_range,
        }

    async def get_available_dates(self) -> List[str]:
        """获取有历史记录的日期列表

        Returns:
            日期字符串列表（降序）；未接入存储返回空列表
        """
        repo = self._repo()
        if repo is None:
            return []
        return await repo.llm_request_available_dates()

    async def get_available_models(self) -> List[str]:
        """获取历史记录中出现过的模型名列表

        Returns:
            模型名列表（去重升序）；未接入存储返回空列表
        """
        repo = self._repo()
        if repo is None:
            return []
        return await repo.llm_request_models()
