"""
事件历史记录服务

为 Dashboard 提供事件环形缓冲存储,并支持可选的每日 JSONL 文件持久化。
由 EventBroadcaster 在广播事件给 WebSocket 客户端之前调用以记录它们。

设计要点:
- 内存中只保留最近 N 条事件(`collections.deque(maxlen=...)`),无外部依赖
- 可选每日滚动文件持久化(`data/events/YYYY-MM-DD.jsonl`)
- 文件写入不阻塞主流程:在事件循环内通过 `asyncio.to_thread` 卸载,
  在无事件循环环境(同步上下文)中同步追加
- 不做单例,由持有者(EventBroadcaster)实例化并注入
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.modules.logging import get_logger


# 默认参数
DEFAULT_MAX_EVENTS = 5000
DEFAULT_PERSIST_DIR = "data/events"
SUMMARY_MAX_LENGTH = 200
ALLOWED_LEVELS = ("info", "warn", "error")


class EventRecord(BaseModel):
    """单条事件记录。

    由 EventBroadcaster 在广播事件前构造并交给 EventHistoryService 存档。

    字段说明:
    - `id`: 唯一标识,默认 uuid4()
    - `type`: 事件类型,如 "message.received" / "decision.intent" / "output.render" /
      "collector.connected" / "system.error"
    - `timestamp`: 事件时刻(Unix 秒),默认 `time.time()`
    - `level`: 严重级别,限定为 "info" | "warn" | "error"
    - `source`: 数据源标识,如 "bili_danmaku" / "maibot" / "dashboard"
    - `summary`: 人类可读的一行摘要,不超过 200 字符
    - `data`: 完整的序列化载荷字典(可能很大)
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="事件唯一 ID(uuid4)")
    type: str = Field(..., description="事件类型,如 message.received / system.error")
    timestamp: float = Field(default_factory=time.time, description="事件时刻,Unix 秒(time.time())")
    level: str = Field(default="info", description="严重级别,info | warn | error")
    source: str = Field(..., description="数据源标识,如 bili_danmaku / maibot / dashboard")
    summary: str = Field(
        default="",
        max_length=SUMMARY_MAX_LENGTH,
        description="人类可读的一行摘要,最大 200 字符",
    )
    data: Dict[str, Any] = Field(default_factory=dict, description="完整序列化载荷字典")

    model_config = ConfigDict(extra="forbid")

    @field_validator("level")
    @classmethod
    def _validate_level(cls, value: str) -> str:
        """强制 `level` 必须是允许的三个字符串之一。"""
        if value not in ALLOWED_LEVELS:
            raise ValueError(f"level must be one of {list(ALLOWED_LEVELS)}, got {value!r}")
        return value


def infer_event_level(event_type: str) -> str:
    """根据事件类型推断严重级别。

    规则(大小写不敏感,首条命中返回):
    - 包含 "error" -> "error"
    - 包含 "disconnect" 或 "shutdown" -> "warn"
    - 其它 -> "info"

    Args:
        event_type: 事件类型名,如 "system.error" / "collector.disconnected"

    Returns:
        三个允许的级别之一。
    """
    lowered = event_type.lower()
    if "error" in lowered:
        return "error"
    if "disconnect" in lowered or "shutdown" in lowered:
        return "warn"
    return "info"


class EventHistoryService:
    """事件环形缓冲历史服务。

    在内存中保留最近 N 条事件(默认 5000),可选择性地把每条事件追加到
    按日滚动的 JSONL 文件(`data/events/YYYY-MM-DD.jsonl`)。

    用法:
    - 由 EventBroadcaster 实例化一个并通过构造器注入
    - 不是单例;多个实例相互独立
    """

    def __init__(
        self,
        max_events: int = DEFAULT_MAX_EVENTS,
        persist: bool = False,
        persist_dir: str = DEFAULT_PERSIST_DIR,
    ) -> None:
        """初始化事件历史服务。

        Args:
            max_events: 环形缓冲容量(deque maxlen),必须为正整数
            persist: 是否启用 JSONL 文件持久化
            persist_dir: 持久化根目录,相对项目根(`Amaidesu/`)

        Raises:
            ValueError: 当 `max_events` 非正数
        """
        if max_events <= 0:
            raise ValueError(f"max_events must be positive, got {max_events}")

        self.max_events: int = max_events
        self.persist: bool = persist
        self.logger = get_logger(self.__class__.__name__)

        # 内存环形缓冲
        self._buffer: Deque[EventRecord] = deque(maxlen=max_events)

        # 持久化目录(懒创建)
        self._persist_dir: Optional[Path] = None
        self._current_date: Optional[str] = None
        self._current_file_path: Optional[Path] = None
        if self.persist:
            project_root = Path(__file__).resolve().parents[3]
            self._persist_dir = (project_root / persist_dir).resolve()
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"事件历史 JSONL 持久化已启用,目录: {self._persist_dir}")

    # ------------------------------------------------------------------ #
    # 内部:日期与文件路径                                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _date_string(timestamp: float) -> str:
        """把 Unix 秒格式化为 `YYYY-MM-DD`。"""
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")

    def _file_path_for_date(self, date_str: str) -> Path:
        """根据日期串构造当日 JSONL 文件的完整路径。"""
        if self._persist_dir is None:
            raise RuntimeError("persist is disabled; _persist_dir is None")
        return self._persist_dir / f"{date_str}.jsonl"

    # ------------------------------------------------------------------ #
    # 公开 API                                                            #
    # ------------------------------------------------------------------ #

    def record(self, event: EventRecord) -> None:
        """记录一条事件到环形缓冲,并在 `persist=True` 时附加到当日 JSONL。

        文件 I/O 不会阻塞调用方:
        - 当存在运行中的事件循环时,append 通过 `asyncio.to_thread` 卸载到线程池
        - 当无事件循环(纯同步上下文)时,降级为同步追加

        Args:
            event: 待记录的事件对象
        """
        # 1) 内存缓冲始终立即写入(deque 自动处理 maxlen 淘汰)
        self._buffer.append(event)

        # 2) 持久化开关关闭时直接返回
        if not self.persist or self._persist_dir is None:
            return

        # 序列化一次,避免在线程中重复构造
        line = event.model_dump_json()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有运行中的事件循环:降级为同步写入(可接受,单行 JSON 很小)
            self._append_to_file(line, event.timestamp)
            return

        # 在事件循环内:fire-and-forget 写入,失败仅记录日志
        loop.create_task(asyncio.to_thread(self._append_to_file, line, event.timestamp))

    def get_recent(self, limit: int = 100) -> List[EventRecord]:
        """返回环形缓冲中最近 `limit` 条事件,按时间倒序(最新在前)。

        Args:
            limit: 返回的最大条数;<=0 时返回空列表

        Returns:
            事件列表(可能少于 limit;若缓冲为空则为空列表)
        """
        if limit <= 0:
            return []
        # 旧 -> 新;倒序后取尾部(最新的)
        return list(self._buffer)[::-1][:limit]

    def query(
        self,
        *,
        types: Optional[List[str]] = None,
        level: Optional[str] = None,
        before_timestamp: Optional[float] = None,
        limit: int = 100,
    ) -> List[EventRecord]:
        """基于内存环形缓冲的过滤查询(不读磁盘)。

        结果按时间倒序(最新在前)。当 `before_timestamp` 指定时,只返回
        严格 `timestamp < before_timestamp` 的事件(用于分页游标)。

        Args:
            types: 事件类型白名单(OR 语义);None 表示不过滤
            level: 精确匹配的严重级别;None 表示不过滤
            before_timestamp: 仅返回时间戳严格小于该值的事件;用于分页
            limit: 返回的最大条数;<=0 时返回空列表

        Returns:
            事件列表(可能少于 limit)
        """
        if limit <= 0:
            return []

        results: List[EventRecord] = []
        # 倒序遍历:最新优先,并配合 limit 提前终止
        for record in reversed(self._buffer):
            if types is not None and record.type not in types:
                continue
            if level is not None and record.level != level:
                continue
            if before_timestamp is not None and record.timestamp >= before_timestamp:
                continue
            results.append(record)
            if len(results) >= limit:
                break
        return results

    def get_statistics(self) -> Dict[str, Any]:
        """聚合当前环形缓冲的统计信息(不读磁盘)。

        Returns:
            包含 `total` / `by_type` / `by_level` / `by_source` / `capacity`
            / `oldest_timestamp` / `newest_timestamp` 的字典
        """
        type_counts: Dict[str, int] = {}
        level_counts: Dict[str, int] = {}
        source_counts: Dict[str, int] = {}

        oldest_ts: Optional[float] = None
        newest_ts: Optional[float] = None

        for record in self._buffer:
            type_counts[record.type] = type_counts.get(record.type, 0) + 1
            level_counts[record.level] = level_counts.get(record.level, 0) + 1
            source_counts[record.source] = source_counts.get(record.source, 0) + 1

            ts = record.timestamp
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts
            if newest_ts is None or ts > newest_ts:
                newest_ts = ts

        return {
            "total": len(self._buffer),
            "capacity": self.max_events,
            "by_type": type_counts,
            "by_level": level_counts,
            "by_source": source_counts,
            "oldest_timestamp": oldest_ts,
            "newest_timestamp": newest_ts,
        }

    def cleanup(self) -> None:
        """释放资源:清空环形缓冲并重置当日文件缓存。

        注意:不会删除磁盘上已写入的 JSONL 文件(由外部策略管理)。
        """
        self._buffer.clear()
        self._current_date = None
        self._current_file_path = None

    # ------------------------------------------------------------------ #
    # 内部:文件 I/O                                                       #
    # ------------------------------------------------------------------ #

    def _append_to_file(self, line: str, timestamp: float) -> None:
        """同步把单个 JSON 行追加到当日 JSONL 文件(`append` 模式)。

        由 `record()` 通过 `asyncio.to_thread` 调用,或在无事件循环时直接调用。
        任何 I/O 失败都只写日志,不抛异常。

        Args:
            line: 已序列化的 JSON 字符串
            timestamp: 用于确定当日文件的时间戳
        """
        if self._persist_dir is None:
            return
        try:
            date_str = self._date_string(timestamp)
            # 日期切换或首次调用 -> 重新计算文件路径
            if date_str != self._current_date or self._current_file_path is None:
                self._current_date = date_str
                self._current_file_path = self._file_path_for_date(date_str)
            with open(self._current_file_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as exc:  # noqa: BLE001 - I/O 失败仅记录
            self.logger.warning(f"写入事件持久化文件失败 ({self._current_file_path}): {exc!r}")


__all__ = [
    "EventRecord",
    "EventHistoryService",
    "infer_event_level",
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_PERSIST_DIR",
    "SUMMARY_MAX_LENGTH",
    "ALLOWED_LEVELS",
]
