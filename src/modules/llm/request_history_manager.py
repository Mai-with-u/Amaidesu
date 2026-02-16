"""
LLM 请求历史记录管理器

负责记录每次 LLM 请求的完整信息，包括请求参数、响应内容、Token 使用量、费用等。

全局管理器使用方式：
1. 直接创建 RequestHistoryManager() - 会自动使用全局实例
2. 使用 get_global_request_history_manager() - 显式获取全局实例
3. 使用 set_global_request_history_manager_callback() - 设置回调函数（用于实时推送）

注意：所有地方都应该使用全局实例以确保数据一致性
"""

import json
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from src.modules.logging import get_logger

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


HISTORY_DIR = "history"
CACHE_SIZE = 100  # 内存缓存大小


class TokenUsage(BaseModel):
    """Token 使用量"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class RequestRecord(BaseModel):
    """LLM 请求记录"""

    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
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
    - 记录每次 LLM 请求的完整信息
    - 支持按日期分文件存储
    - 内存缓存加速最近请求的查询
    - 支持分页、筛选功能
    """

    def __init__(
        self,
        record_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        use_global: bool = True,
        cache_size: int = CACHE_SIZE,
    ):
        """初始化请求历史记录管理器

        Args:
            record_callback: 记录更新时的回调函数，参数为请求记录字典
            use_global: 是否使用全局实例
            cache_size: 内存缓存大小
        """
        # 如果使用全局实例且已存在，则返回现有实例
        global global_request_history_manager
        if use_global and global_request_history_manager is not None:
            if record_callback:
                global_request_history_manager.record_callback = record_callback
            self.__dict__.update(global_request_history_manager.__dict__)
            return

        # 获取项目根目录
        project_root = Path(__file__).parent
        self.history_dir = project_root / HISTORY_DIR
        self.history_dir.mkdir(exist_ok=True)

        # 初始化 logger
        self.logger = get_logger("RequestHistoryManager")

        # 设置回调
        self.record_callback = record_callback

        # 内存缓存（使用 deque 限制大小）
        self._cache: deque = deque(maxlen=cache_size)

        # 缓存当前日期的文件路径
        self._current_date: Optional[str] = None
        self._current_file_path: Optional[Path] = None

        # 如果使用全局实例，保存到全局变量
        if use_global:
            global_request_history_manager = self

    def _get_date_string(self, timestamp_ms: Optional[int] = None) -> str:
        """获取日期字符串

        Args:
            timestamp_ms: 毫秒时间戳，默认为当前时间

        Returns:
            日期字符串，格式为 YYYY-MM-DD
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)
        return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d")

    def _get_history_file_path(self, date_str: str) -> Path:
        """获取指定日期的历史文件路径

        Args:
            date_str: 日期字符串，格式为 YYYY-MM-DD

        Returns:
            历史文件路径
        """
        return self.history_dir / f"{date_str}.json"

    def _load_history_file(self, file_path: Path) -> List[Dict[str, Any]]:
        """加载历史文件

        Args:
            file_path: 历史文件路径

        Returns:
            请求记录列表
        """
        if not file_path.exists():
            return []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError) as e:
            self.logger.warning(f"读取历史文件失败 {file_path}: {e}")
            return []

    def _save_history_file(self, file_path: Path, records: List[Dict[str, Any]]) -> None:
        """保存历史文件

        Args:
            file_path: 历史文件路径
            records: 请求记录列表
        """
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
        except IOError as e:
            self.logger.error(f"保存历史文件失败 {file_path}: {e}")

    def record_request(self, record: RequestRecord) -> str:
        """记录一次请求

        Args:
            record: 请求记录对象

        Returns:
            请求 ID
        """
        date_str = self._get_date_string(record.timestamp)
        file_path = self._get_history_file_path(date_str)

        # 加载现有记录
        records = self._load_history_file(file_path)

        # 添加新记录
        record_dict = record.to_dict()
        records.append(record_dict)

        # 保存文件
        self._save_history_file(file_path, records)

        # 更新内存缓存
        self._cache.append(record_dict)

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

    def get_request_by_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取单个请求详情

        Args:
            request_id: 请求 ID

        Returns:
            请求记录字典，未找到返回 None
        """
        # 先在缓存中查找
        for record in self._cache:
            if record.get("request_id") == request_id:
                return record

        # 遍历所有历史文件查找
        for file_path in self.history_dir.glob("*.json"):
            records = self._load_history_file(file_path)
            for record in records:
                if record.get("request_id") == request_id:
                    return record

        return None

    def get_history(
        self,
        client_type: Optional[str] = None,
        model_name: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        success_only: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """获取历史列表

        Args:
            client_type: 客户端类型筛选
            model_name: 模型名称筛选
            start_time: 开始时间（毫秒时间戳）
            end_time: 结束时间（毫秒时间戳）
            success_only: 是否只返回成功的请求
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            包含 records, total, page, page_size 的字典
        """
        all_records: List[Dict[str, Any]] = []

        # 确定需要加载的文件
        if start_time and end_time:
            start_date = self._get_date_string(start_time)
            end_date = self._get_date_string(end_time)
            date_range = self._get_date_range(start_date, end_date)
        elif start_time:
            start_date = self._get_date_string(start_time)
            today = self._get_date_string()
            date_range = self._get_date_range(start_date, today)
        elif end_time:
            end_date = self._get_date_string(end_time)
            date_range = [end_date]  # 如果只有结束时间，只查那一天
        else:
            date_range = None  # 查询所有文件

        # 加载记录
        if date_range:
            for date_str in date_range:
                file_path = self._get_history_file_path(date_str)
                if file_path.exists():
                    all_records.extend(self._load_history_file(file_path))
        else:
            # 加载所有文件
            for file_path in sorted(self.history_dir.glob("*.json"), reverse=True):
                all_records.extend(self._load_history_file(file_path))

        # 应用筛选
        filtered_records = []
        for record in all_records:
            # 客户端类型筛选
            if client_type and record.get("client_type") != client_type:
                continue
            # 模型名称筛选
            if model_name and record.get("model_name") != model_name:
                continue
            # 时间范围筛选
            if start_time and record.get("timestamp", 0) < start_time:
                continue
            if end_time and record.get("timestamp", 0) > end_time:
                continue
            # 成功状态筛选
            if success_only is not None and record.get("success") != success_only:
                continue
            filtered_records.append(record)

        # 按时间戳降序排序（最新的在前）
        filtered_records.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

        # 分页
        total = len(filtered_records)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_records = filtered_records[start_idx:end_idx]

        return {
            "records": paginated_records,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
        }

    def _get_date_range(self, start_date: str, end_date: str) -> List[str]:
        """获取日期范围列表

        Args:
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)

        Returns:
            日期字符串列表
        """
        from datetime import datetime, timedelta

        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        date_list = []
        current = start
        while current <= end:
            date_list.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        return date_list

    def clear_history(
        self,
        before_date: Optional[str] = None,
        confirm: bool = False,
    ) -> Dict[str, Any]:
        """清空历史记录

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

        cleared_files = 0
        cleared_records = 0

        try:
            if before_date is None:
                # 清除所有历史文件
                for file_path in self.history_dir.glob("*.json"):
                    records = self._load_history_file(file_path)
                    cleared_records += len(records)
                    file_path.unlink()
                    cleared_files += 1
                # 清空缓存
                self._cache.clear()
            else:
                # 清除指定日期之前的文件
                for file_path in self.history_dir.glob("*.json"):
                    file_date = file_path.stem  # 文件名即日期
                    if file_date < before_date:
                        records = self._load_history_file(file_path)
                        cleared_records += len(records)
                        file_path.unlink()
                        cleared_files += 1

                # 清理缓存中过期的记录
                before_timestamp = int(datetime.strptime(before_date, "%Y-%m-%d").timestamp() * 1000)
                self._cache = deque(
                    (r for r in self._cache if r.get("timestamp", 0) >= before_timestamp),
                    maxlen=self._cache.maxlen,
                )

            self.logger.info(f"已清除 {cleared_files} 个历史文件，共 {cleared_records} 条记录")

            return {
                "success": True,
                "cleared_files": cleared_files,
                "cleared_records": cleared_records,
                "message": f"成功清除 {cleared_files} 个文件，{cleared_records} 条记录",
            }

        except Exception as e:
            self.logger.error(f"清除历史记录失败: {e}")
            return {
                "success": False,
                "message": f"清除失败: {e}",
            }

    def get_statistics(
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
        # 获取所有符合条件的记录
        result = self.get_history(
            start_time=start_time,
            end_time=end_time,
            page=1,
            page_size=10000,  # 获取足够多的记录用于统计
        )
        records = result["records"]

        # 计算统计信息
        total_requests = len(records)
        successful_requests = sum(1 for r in records if r.get("success"))
        failed_requests = total_requests - successful_requests

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

            # 按模型统计
            model_name = record.get("model_name", "unknown")
            if model_name not in model_stats:
                model_stats[model_name] = {
                    "count": 0,
                    "total_tokens": 0,
                    "total_cost": 0.0,
                }
            model_stats[model_name]["count"] += 1
            model_stats[model_name]["total_tokens"] += usage.get("total_tokens", 0)
            model_stats[model_name]["total_cost"] += record.get("cost", 0)

            # 按客户端类型统计
            client_type = record.get("client_type", "unknown")
            client_stats[client_type] = client_stats.get(client_type, 0) + 1

        return {
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": failed_requests,
            "success_rate": successful_requests / total_requests if total_requests > 0 else 0,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
            "avg_latency_ms": total_latency / total_requests if total_requests > 0 else 0,
            "model_stats": model_stats,
            "client_stats": client_stats,
            "time_range": {
                "start": start_time,
                "end": end_time,
            },
        }

    def get_available_dates(self) -> List[str]:
        """获取有历史记录的日期列表

        Returns:
            日期字符串列表（降序）
        """
        dates = []
        for file_path in self.history_dir.glob("*.json"):
            dates.append(file_path.stem)
        return sorted(dates, reverse=True)

    def get_cache_size(self) -> int:
        """获取当前缓存大小

        Returns:
            缓存中的记录数
        """
        return len(self._cache)
