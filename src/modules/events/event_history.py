"""
事件历史记录服务

为 Dashboard 提供事件环形缓冲存储：事件日志定位为"运行周期观察窗"，
纯内存、重启即清。持久观察诉求由各自的事实源承担（消息流落 live_chat
等业务表），本服务不复制数据。

设计要点:
- 内存中只保留最近 N 条事件(``collections.deque(maxlen=...)``)，供
  Dashboard 热路径查询（recent / 游标续传 / 按场次过滤）
- 不做单例，由持有者（EventBroadcaster / main 组合根）实例化并注入
"""

from __future__ import annotations

import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms


# 默认参数
DEFAULT_MAX_EVENTS = 5000
SUMMARY_MAX_LENGTH = 200
ALLOWED_LEVELS = ("info", "warn", "error")


class EventRecord(BaseModel):
    """单条事件记录。

    由 EventBroadcaster 在广播事件前构造并交给 EventHistoryService 存档。

    字段说明:
    - `id`: 唯一标识,默认 uuid4()
    - `type`: 事件类型名,如 "room.message" / "planner.decision"（广播兼容名;
      事件名直通,唯一例外 room.message.* 折叠）
    - `event_name`: EventBus 精确事件名（如 ``room.message.danmaku``）；
      空字符串表示未知,落库时退回 `type`
    - `timestamp_ms`: 事件时刻(Unix 毫秒),默认 `now_ms()`
    - `level`: 严重级别,限定为 "info" | "warn" | "error"
    - `source`: 数据源标识,如 "bili_danmaku" / "dashboard"
    - `summary`: 人类可读的一行摘要,不超过 200 字符
    - `data`: 完整的序列化载荷字典(可能很大)
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="事件唯一 ID(uuid4)")
    type: str = Field(..., description="事件类型名,如 room.message / planner.decision")
    event_name: str = Field(default="", description="EventBus 精确事件名;空则落库退回 type")
    timestamp_ms: int = Field(default_factory=now_ms, description="事件时刻,Unix 毫秒")
    level: str = Field(default="info", description="严重级别,info | warn | error")
    source: str = Field(..., description="数据源标识,如 bili_danmaku / dashboard")
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
        event_type: 事件类型名,如 "core.error" / "collector.disconnected"

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

    在内存中保留最近 N 条事件(默认 5000)，超限自动淘汰最旧记录。

    用法:
    - 由组合根实例化，再交给 EventHistoryRecorder
    - 不是单例;多个实例相互独立
    """

    def __init__(self, max_events: int = DEFAULT_MAX_EVENTS) -> None:
        """初始化事件历史服务。

        Args:
            max_events: 环形缓冲容量(deque maxlen),必须为正整数

        Raises:
            ValueError: 当 `max_events` 非正数
        """
        if max_events <= 0:
            raise ValueError(f"max_events must be positive, got {max_events}")

        self.max_events: int = max_events
        self.logger = get_logger(self.__class__.__name__)

        # 内存环形缓冲
        self._buffer: Deque[EventRecord] = deque(maxlen=max_events)

    # ------------------------------------------------------------------ #
    # 公开 API                                                            #
    # ------------------------------------------------------------------ #

    def record(self, event: EventRecord) -> None:
        """记录一条事件到环形缓冲(deque 自动处理 maxlen 淘汰)。"""
        self._buffer.append(event)

    def get_recent(self, limit: int = 100) -> List[EventRecord]:
        """返回环形缓冲中最近 `limit` 条事件,按时间倒序(最新在前)。"""
        if limit <= 0:
            return []
        # 旧 -> 新;倒序后取尾部(最新的)
        return list(self._buffer)[::-1][:limit]

    def get_since(self, event_id: str, limit: int = 500) -> List[EventRecord]:
        """游标续传：返回 `event_id` 之后（不含）的事件，按时间正序（旧→新）。

        用于客户端断线/刷新后按游标补缺口。游标未命中（过旧被环形缓冲淘汰
        或未知 id）时退化为最近 `limit` 条——客户端按 id 去重合并，语义仍正确。
        """
        if limit <= 0:
            return []
        buffer = list(self._buffer)
        cursor = -1
        for index in range(len(buffer) - 1, -1, -1):
            if buffer[index].id == event_id:
                cursor = index
                break
        if cursor == -1:
            return buffer[-limit:]
        return buffer[cursor + 1 :][-limit:]

    def get_by_session(self, live_session_id: int, limit: int = 500) -> List[EventRecord]:
        """按场次主键过滤缓冲事件，按时间正序（旧→新）。

        命中条件：事件 data 携带 ``live_session_id`` 且等于给定主键
        （场次盖章拦截器保证业务事件统一携带）。供单场时间线回看。
        """
        if limit <= 0:
            return []
        hits = [
            record
            for record in self._buffer
            if isinstance(record.data, dict) and record.data.get("live_session_id") == live_session_id
        ]
        return hits[-limit:]

    def query(
        self,
        *,
        types: Optional[List[str]] = None,
        level: Optional[str] = None,
        before_timestamp_ms: Optional[int] = None,
        limit: int = 100,
    ) -> List[EventRecord]:
        """基于内存环形缓冲的过滤查询(不读库)。

        结果按时间倒序(最新在前)。当 `before_timestamp_ms` 指定时,只返回
        严格 `timestamp_ms < before_timestamp_ms` 的事件(用于分页游标)。
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
            if before_timestamp_ms is not None and record.timestamp_ms >= before_timestamp_ms:
                continue
            results.append(record)
            if len(results) >= limit:
                break
        return results

    def get_statistics(self) -> Dict[str, Any]:
        """聚合当前环形缓冲的统计信息(不读库)。"""
        type_counts: Dict[str, int] = {}
        level_counts: Dict[str, int] = {}
        source_counts: Dict[str, int] = {}

        oldest_ts: Optional[int] = None
        newest_ts: Optional[int] = None

        for record in self._buffer:
            type_counts[record.type] = type_counts.get(record.type, 0) + 1
            level_counts[record.level] = level_counts.get(record.level, 0) + 1
            source_counts[record.source] = source_counts.get(record.source, 0) + 1

            ts = record.timestamp_ms
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
            "oldest_timestamp_ms": oldest_ts,
            "newest_timestamp_ms": newest_ts,
        }

    def cleanup(self) -> None:
        """释放资源:清空环形缓冲。"""
        self._buffer.clear()


__all__ = [
    "EventRecord",
    "EventHistoryService",
    "infer_event_level",
    "DEFAULT_MAX_EVENTS",
    "SUMMARY_MAX_LENGTH",
    "ALLOWED_LEVELS",
]
