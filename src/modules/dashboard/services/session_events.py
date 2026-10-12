"""场次回看的事件侧读取面：内存环形缓冲 ∪ 诊断事件流文件。

回看时间线的事件事实源：``EventHistoryRecorder`` 订阅全部事件并经
``DiagnosticsWriter`` 逐小时落盘（``logs/events/YYYY-MM-DD/HH.jsonl``），
覆盖重启前的完整场次；内存环形缓冲只保留本次运行的观察窗。本模块把两个
来源按记录 id 去重合并、按白名单过滤后交给时间线装配，使回看与实时视图
呈现同一卡面。

诊断文件是可删除的旁路数据（用户手动管理空间）：目录缺失、文件损坏、
单行解析失败一律按"无数据"跳过并如实回报（``days_pruned``），回看相应
降级为明细行视图，绝不抛错阻断。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

from src.modules.events.event_history import EventRecord
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.events.event_history import EventHistoryService

logger = get_logger("SessionEventReader")

# 回看时间线收录的事件类型（与实时 WS 卡面对齐）：
# 精确名单收状态与决策事实——消息/发言/礼物/SC 已由明细行承载，不重复收录；
# task.changed 非时间线卡面（实时侧由任务账本渲染），不收录
REPLAY_EVENT_TYPES = frozenset(
    {
        "planner.decision",
        "planner.verdict",
        "streamer.stage",
        "live.started",
        "live.ended",
        "rundown.changed",
        "game.report",
        "game.attention_required",
        "game.error",
        "game.milestone",
        "agent.prompted",
        "agent.replied",
    }
)

# 动态族前缀：工具结果按工具名逐事件落盘（tool.result.<tool_name>）
REPLAY_EVENT_PREFIXES = ("tool.result.",)

# 单条记录 data 内超长字符串的截断上限（工具结果正文可达数百 KB，回看卡片
# 只展示预览；截断只发生在返回侧，不改动落盘文件）
MAX_FIELD_STRING_LEN = 8_000
_TRUNCATION_SUFFIX = "…[已截断]"

# 扫描的天数上界：防异常长场次（如忘结束的进行中场次）把日期枚举拖成灾
_MAX_SCAN_DAYS = 370


def is_replay_event_type(event_type: str) -> bool:
    """事件类型是否在回看时间线收录范围（精确名单或工具结果动态族）。"""
    if event_type in REPLAY_EVENT_TYPES:
        return True
    return event_type.startswith(REPLAY_EVENT_PREFIXES)


def trim_long_strings(value: Any) -> Any:
    """递归截断载荷中的超长字符串（返回新结构，不改入参）。"""
    if isinstance(value, str) and len(value) > MAX_FIELD_STRING_LEN:
        return value[:MAX_FIELD_STRING_LEN] + _TRUNCATION_SUFFIX
    if isinstance(value, list):
        return [trim_long_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: trim_long_strings(item) for key, item in value.items()}
    return value


@dataclass
class SessionEventWindow:
    """一次场次事件收集的结果（升序、去重、白名单过滤后）。"""

    records: List[EventRecord] = field(default_factory=list)
    """场次时间范围内存在缺失的诊断天目录（被清理或从未落盘）——事件侧降级信号"""
    days_pruned: bool = False


class SessionEventReader:
    """合并环形缓冲与诊断文件的场次事件收集器。

    两个来源可任缺其一：``events_dir`` 为 None（诊断未装配）时只读环形缓冲，
    ``event_history`` 为 None（极简装配）时只读文件；两者都缺则返回空窗口。
    """

    def __init__(
        self,
        event_history: Optional["EventHistoryService"] = None,
        events_dir: Optional[Path] = None,
    ) -> None:
        self.event_history = event_history
        self.events_dir = events_dir

    def collect(
        self,
        *,
        live_session_id: int,
        started_at_ms: int,
        ended_at_ms: Optional[int],
        before_timestamp_ms: Optional[int] = None,
    ) -> SessionEventWindow:
        """收集单场事件记录（时间升序）。

        ``before_timestamp_ms`` 游标只保留严格早于该时刻的记录（向前翻页）。
        环形缓冲与文件两侧按记录 id 去重（id 为空的记录退回
        ``type@timestamp_ms`` 键），当前运行中的事件两侧并存时不重复。
        """
        merged: dict[str, EventRecord] = {}

        if self.event_history is not None:
            # 环形缓冲容量即上限：按场过滤后全量取出，与文件侧去重合并
            for record in self.event_history.get_by_session(live_session_id, limit=10_000):
                if self._in_window(record, before_timestamp_ms):
                    merged[self._dedupe_key(record)] = record

        days_pruned = False
        if self.events_dir is not None:
            records, days_pruned = self._collect_from_files(
                live_session_id=live_session_id,
                started_at_ms=started_at_ms,
                ended_at_ms=ended_at_ms,
                before_timestamp_ms=before_timestamp_ms,
            )
            for record in records:
                merged[self._dedupe_key(record)] = record

        ordered = sorted(merged.values(), key=lambda record: record.timestamp_ms)
        return SessionEventWindow(records=ordered, days_pruned=days_pruned)

    # ------------------------------------------------------------------ #
    # 内部实现                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _in_window(record: EventRecord, before_timestamp_ms: Optional[int]) -> bool:
        if before_timestamp_ms is not None and record.timestamp_ms >= before_timestamp_ms:
            return False
        return True

    @staticmethod
    def _dedupe_key(record: EventRecord) -> str:
        if record.id:
            return record.id
        return f"{record.type}@{record.timestamp_ms}"

    def _collect_from_files(
        self,
        *,
        live_session_id: int,
        started_at_ms: int,
        ended_at_ms: Optional[int],
        before_timestamp_ms: Optional[int],
    ) -> tuple[List[EventRecord], bool]:
        events_dir = self.events_dir
        if events_dir is None or not events_dir.is_dir():
            # 诊断根目录都不存在：视为全部缺失（降级信号），非错误
            return [], True

        window_end_ms = ended_at_ms if ended_at_ms is not None else now_ms()
        first_day = datetime.fromtimestamp(started_at_ms / 1000).date()
        last_day = datetime.fromtimestamp(window_end_ms / 1000).date()
        if (last_day - first_day).days > _MAX_SCAN_DAYS:
            last_day = first_day + timedelta(days=_MAX_SCAN_DAYS)
            logger.warning(f"场次 {live_session_id} 时间跨度超过 {_MAX_SCAN_DAYS} 天，事件扫描截断至该上界")

        records: List[EventRecord] = []
        days_pruned = False
        day = first_day
        while day <= last_day:
            day_dir = events_dir / day.strftime("%Y-%m-%d")
            if not day_dir.is_dir():
                days_pruned = True
            else:
                for hour_file in sorted(day_dir.glob("*.jsonl")):
                    records.extend(
                        self._read_hour_file(
                            hour_file,
                            live_session_id=live_session_id,
                            before_timestamp_ms=before_timestamp_ms,
                        )
                    )
            day += timedelta(days=1)

        records.sort(key=lambda record: record.timestamp_ms)
        return records, days_pruned

    def _read_hour_file(
        self,
        path: Path,
        *,
        live_session_id: int,
        before_timestamp_ms: Optional[int],
    ) -> List[EventRecord]:
        records: List[EventRecord] = []
        malformed = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError("记录行不是 JSON 对象")
                        data = row.get("data")
                        if not isinstance(data, dict) or data.get("live_session_id") != live_session_id:
                            continue
                        if before_timestamp_ms is not None and int(row.get("timestamp_ms", 0)) >= before_timestamp_ms:
                            continue
                        if not is_replay_event_type(str(row.get("type", ""))):
                            continue
                        records.append(EventRecord.model_validate(row))
                    except Exception:
                        malformed += 1
        except OSError as e:
            logger.warning(f"读取诊断事件文件失败，跳过: {path} ({e})")
            return records
        if malformed:
            logger.debug(f"诊断事件文件存在无法解析的行（已跳过 {malformed} 行）: {path}")
        return records


__all__ = [
    "SessionEventReader",
    "SessionEventWindow",
    "is_replay_event_type",
    "trim_long_strings",
    "REPLAY_EVENT_TYPES",
    "REPLAY_EVENT_PREFIXES",
    "MAX_FIELD_STRING_LEN",
]
