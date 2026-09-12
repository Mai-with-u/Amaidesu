"""
LLM 请求历史记录管理器

负责记录每次 LLM 请求的完整信息，包括请求参数、响应内容、Token 使用量、费用等。
持久化目标是 SQLite ``llm_requests`` 表（组合根经 ``attach_store`` 注入）；
未注入时仅保留内存环形缓存（查询范围随之受限）。

全局管理器使用方式：
1. 直接创建 RequestHistoryManager() - 会自动使用全局实例
2. 使用 get_global_request_history_manager() - 显式获取全局实例
3. 使用 set_global_request_history_manager_callback() - 设置回调函数（用于实时推送）

注意：所有地方都应该使用全局实例以确保数据一致性
"""

import asyncio
import json
import uuid
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.storage.sqlite_store import SQLiteStore

# 全局请求历史记录管理器实例
global_request_history_manager: Optional["RequestHistoryManager"] = None


def get_global_request_history_manager() -> "RequestHistoryManager":
    """获取全局请求历史记录管理器实例

    这是获取全局请求历史记录管理器的推荐方式。
    确保所有地方都使用同一个实例以保持数据一致性。
    """
    global global_request_history_manager
    if global_request_history_manager is None:
        global_request_history_manager = RequestHistoryManager(use_global=False)
    return global_request_history_manager


def set_global_request_history_manager_callback(callback: Optional[Callable[[Dict[str, Any]], None]]) -> None:
    """设置全局请求历史记录管理器的回调

    Args:
        callback: 回调函数，参数为请求记录字典
    """
    global global_request_history_manager
    if global_request_history_manager is None:
        global_request_history_manager = RequestHistoryManager(record_callback=callback)
    else:
        global_request_history_manager.record_callback = callback


CACHE_SIZE = 100  # 内存缓存大小


class TokenUsage(BaseModel):
    """Token 使用量"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class RequestRecord(BaseModel):
    """LLM 请求记录"""

    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")
    timestamp: int = Field(default_factory=lambda: now_ms())
    client_type: str  # llm, llm_fast, vlm, llm_local
    model_name: str
    request_params: Dict[str, Any] = Field(default_factory=dict)
    response_content: Optional[str] = None
    reasoning_content: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    usage: Optional[TokenUsage] = None
    cost: float = 0.0
    success: bool = True
    error: Optional[str] = None
    latency_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "client_type": self.client_type,
            "model_name": self.model_name,
            "request_params": self.request_params,
            "response_content": self.response_content,
            "reasoning_content": self.reasoning_content,
            "tool_calls": self.tool_calls,
            "usage": self.usage.model_dump() if self.usage else None,
            "cost": self.cost,
            "success": self.success,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


class HistoryFilter(BaseModel):
    """历史记录查询过滤器"""

    client_type: Optional[str] = None
    model_name: Optional[str] = None
    start_time: Optional[int] = None  # 毫秒时间戳
    end_time: Optional[int] = None  # 毫秒时间戳
    success_only: Optional[bool] = None
    page: int = 1
    page_size: int = 50


class RequestHistoryManager:
    """LLM 请求历史记录管理器

    功能：
    - 记录每次 LLM 请求的完整信息（内存缓存 + SQLite ``llm_requests`` 表）
    - 写库经事件循环 fire-and-forget，失败仅告警，不影响 LLM 调用链
    - 支持分页、筛选功能（有存储时走 SQL；无存储时查内存缓存）
    """

    def __init__(
        self,
        record_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        use_global: bool = True,
        cache_size: int = CACHE_SIZE,
        enabled: bool = True,
        sqlite_store: Optional["SQLiteStore"] = None,
    ):
        """初始化请求历史记录管理器

        Args:
            record_callback: 记录更新时的回调函数，参数为请求记录字典
            use_global: 是否使用全局实例
            cache_size: 内存缓存大小
            enabled: 是否启用记录功能。测试环境关闭以避免污染数据库
            sqlite_store: 持久化目标；未注入时仅内存缓存（可后续 attach_store）
        """
        # 如果使用全局实例且已存在，则返回现有实例
        global global_request_history_manager
        if use_global and global_request_history_manager is not None:
            if record_callback:
                global_request_history_manager.record_callback = record_callback
            self.__dict__.update(global_request_history_manager.__dict__)
            return

        # 初始化 logger
        self.logger = get_logger("RequestHistoryManager")

        # 设置回调
        self.record_callback = record_callback

        # 记录开关（测试环境关闭）
        self.enabled = enabled

        # 持久化目标
        self._sqlite_store = sqlite_store

        # 内存缓存（使用 deque 限制大小）
        self._cache: deque = deque(maxlen=cache_size)

        # 如果使用全局实例，保存到全局变量
        if use_global:
            global_request_history_manager = self

    def attach_store(self, sqlite_store: Optional["SQLiteStore"]) -> None:
        """组合根注入持久化目标（全局单例可能先于组合根被惰性创建）。"""
        self._sqlite_store = sqlite_store

    @property
    def sqlite_store(self) -> Optional["SQLiteStore"]:
        """当前注入的持久化目标（测试可读）。"""
        return self._sqlite_store

    # -------------------- 写入 --------------------

    def record_request(self, record: RequestRecord) -> str:
        """记录一次请求

        Args:
            record: 请求记录对象

        Returns:
            请求 ID
        """
        if not self.enabled:
            return record.request_id

        record_dict = record.to_dict()

        # 更新内存缓存
        self._cache.append(record_dict)

        # 异步写库（fire-and-forget；无事件循环的同步上下文仅保留内存缓存）
        if self._sqlite_store is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                loop.create_task(self._persist_request(record_dict))

        # 触发回调
        if self.record_callback:
            try:
                self.record_callback(record_dict)
            except Exception as e:
                self.logger.warning(f"执行记录回调失败: {e}")

        self.logger.debug(
            f"记录请求: {record.request_id}, 模型: {record.model_name}, "
            f"耗时: {record.latency_ms}ms, 成功: {record.success}"
        )
        return record.request_id

    async def _persist_request(self, record_dict: Dict[str, Any]) -> None:
        """写单条请求到 ``llm_requests`` 表；失败仅告警（记账旁路语义）。"""
        usage = record_dict.get("usage") or {}
        try:
            await self._sqlite_store.insert_llm_request(
                request_id=record_dict["request_id"],
                timestamp_ms=record_dict.get("timestamp", 0),
                client_type=record_dict.get("client_type", ""),
                model_name=record_dict.get("model_name", ""),
                request_params_json=json.dumps(
                    record_dict.get("request_params") or {}, ensure_ascii=False, default=str
                ),
                response_content=record_dict.get("response_content"),
                reasoning_content=record_dict.get("reasoning_content"),
                tool_calls_json=json.dumps(record_dict.get("tool_calls") or [], ensure_ascii=False, default=str),
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                total_tokens=int(usage.get("total_tokens", 0)),
                cost=float(record_dict.get("cost", 0.0)),
                success=bool(record_dict.get("success", True)),
                error=record_dict.get("error"),
                latency_ms=int(record_dict.get("latency_ms", 0)),
            )
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志
            self.logger.warning(f"请求历史写库失败: {exc}")

    def record_request_from_dict(self, data: Dict[str, Any]) -> str:
        """从字典记录一次请求

        Args:
            data: 请求记录字典

        Returns:
            请求 ID
        """
        # 处理 usage 字段
        if "usage" in data and isinstance(data["usage"], dict):
            data["usage"] = TokenUsage(**data["usage"])

        record = RequestRecord(**data)
        return self.record_request(record)

    # -------------------- 查询 --------------------

    @staticmethod
    def _row_to_record(row: Dict[str, Any]) -> Dict[str, Any]:
        """DB 行 → 历史记录字典（usage 嵌套与 JSON 列还原）。"""
        usage = None
        if row.get("prompt_tokens") or row.get("completion_tokens") or row.get("total_tokens"):
            usage = {
                "prompt_tokens": int(row.get("prompt_tokens", 0)),
                "completion_tokens": int(row.get("completion_tokens", 0)),
                "total_tokens": int(row.get("total_tokens", 0)),
            }

        def _load_json(text: Any, fallback: Any) -> Any:
            if not text:
                return fallback
            try:
                return json.loads(text)
            except (TypeError, json.JSONDecodeError):
                return fallback

        return {
            "request_id": row.get("request_id", ""),
            "timestamp": row.get("timestamp_ms", 0),
            "client_type": row.get("client_type", ""),
            "model_name": row.get("model_name", ""),
            "request_params": _load_json(row.get("request_params"), {}),
            "response_content": row.get("response_content"),
            "reasoning_content": row.get("reasoning_content"),
            "tool_calls": _load_json(row.get("tool_calls"), []),
            "usage": usage,
            "cost": float(row.get("cost", 0.0)),
            "success": bool(row.get("success", 1)),
            "error": row.get("error"),
            "latency_ms": int(row.get("latency_ms", 0)),
        }

    async def get_request_by_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取单个请求详情

        Args:
            request_id: 请求 ID

        Returns:
            请求记录字典，未找到返回 None
        """
        if self._sqlite_store is not None:
            row = await self._sqlite_store.get_llm_request_by_id(request_id)
            return self._row_to_record(row) if row is not None else None

        # 无存储：查内存缓存
        for record in self._cache:
            if record.get("request_id") == request_id:
                return record
        return None

    async def get_history(
        self,
        client_type: Optional[str] = None,
        model_name: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        success_only: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """获取历史列表（时间倒序分页）

        Args:
            client_type: 客户端类型筛选
            model_name: 模型名称筛选
            start_time: 开始时间（毫秒时间戳）
            end_time: 结束时间（毫秒时间戳）
            success_only: 是否只返回成功的请求
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            包含 records, total, page, page_size, total_pages 的字典
        """
        if self._sqlite_store is not None:
            result = await self._sqlite_store.query_llm_requests(
                client_type=client_type,
                model_name=model_name,
                start_time=start_time,
                end_time=end_time,
                success_only=success_only,
                page=page,
                page_size=page_size,
            )
            records = [self._row_to_record(row) for row in result["rows"]]
            total = int(result["total"])
        else:
            # 无存储：过滤内存缓存
            filtered = []
            for record in self._cache:
                if client_type and record.get("client_type") != client_type:
                    continue
                if model_name and record.get("model_name") != model_name:
                    continue
                if start_time is not None and record.get("timestamp", 0) < start_time:
                    continue
                if end_time is not None and record.get("timestamp", 0) > end_time:
                    continue
                if success_only is not None and record.get("success") != success_only:
                    continue
                filtered.append(record)
            filtered.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            total = len(filtered)
            start_idx = (page - 1) * page_size
            records = filtered[start_idx : start_idx + page_size]

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

        if self._sqlite_store is None:
            return {
                "success": False,
                "message": "未接入持久化存储，无历史可清除",
            }

        try:
            deleted = await self._sqlite_store.delete_llm_requests_before(before_date=before_date)

            # 同步修剪内存缓存
            if before_date is None:
                self._cache.clear()
            else:
                cutoff_ms = int(datetime.strptime(before_date, "%Y-%m-%d").timestamp() * 1000)
                self._cache = deque(
                    (r for r in self._cache if r.get("timestamp", 0) >= cutoff_ms),
                    maxlen=self._cache.maxlen,
                )

            self.logger.info(f"已清除 {deleted} 条请求历史记录")
            return {
                "success": True,
                "cleared_records": deleted,
                "message": f"成功清除 {deleted} 条记录",
            }
        except Exception as e:
            self.logger.error(f"清除历史记录失败: {e}")
            return {
                "success": False,
                "message": f"清除失败: {e}",
            }

    @staticmethod
    def _statistics_from_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """对记录列表做统计聚合（无存储时的内存回退路径）。"""
        total_requests = len(records)
        successful_requests = sum(1 for r in records if r.get("success"))

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        total_cost = 0.0
        total_latency = 0

        model_stats: Dict[str, Dict[str, Any]] = {}
        client_stats: Dict[str, int] = {}

        for record in records:
            usage = record.get("usage") or {}
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)
            total_tokens += usage.get("total_tokens", 0)
            total_cost += record.get("cost", 0)
            total_latency += record.get("latency_ms", 0)

            model_name = record.get("model_name", "unknown")
            if model_name not in model_stats:
                model_stats[model_name] = {"count": 0, "total_tokens": 0, "total_cost": 0.0}
            model_stats[model_name]["count"] += 1
            model_stats[model_name]["total_tokens"] += usage.get("total_tokens", 0)
            model_stats[model_name]["total_cost"] += record.get("cost", 0)

            client_type = record.get("client_type", "unknown")
            client_stats[client_type] = client_stats.get(client_type, 0) + 1

        return {
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": total_requests - successful_requests,
            "success_rate": successful_requests / total_requests if total_requests > 0 else 0,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
            "avg_latency_ms": total_latency / total_requests if total_requests > 0 else 0,
            "model_stats": model_stats,
            "client_stats": client_stats,
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

        if self._sqlite_store is None:
            stats = self._statistics_from_records(list(self._cache))
            stats["time_range"] = time_range
            return stats

        stats = await self._sqlite_store.llm_request_statistics(start_time=start_time, end_time=end_time)
        overall = stats["overall"]
        total_requests = int(overall.get("total", 0))
        successful_requests = int(overall.get("success_count", 0))

        model_stats: Dict[str, Dict[str, Any]] = {}
        for row in stats["by_model"]:
            model_stats[row.get("model_name") or "unknown"] = {
                "count": int(row.get("count", 0)),
                "total_tokens": int(row.get("total_tokens", 0)),
                "total_cost": float(row.get("total_cost", 0.0)),
            }

        client_stats: Dict[str, int] = {
            (row.get("client_type") or "unknown"): int(row.get("count", 0)) for row in stats["by_client"]
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
            "avg_latency_ms": float(overall.get("avg_latency", 0.0)),
            "model_stats": model_stats,
            "client_stats": client_stats,
            "time_range": time_range,
        }

    async def get_available_dates(self) -> List[str]:
        """获取有历史记录的日期列表

        Returns:
            日期字符串列表（降序）；无存储时返回空列表
        """
        if self._sqlite_store is None:
            return []
        return await self._sqlite_store.llm_request_available_dates()

    def get_cache_size(self) -> int:
        """获取当前缓存大小

        Returns:
            缓存中的记录数
        """
        return len(self._cache)
