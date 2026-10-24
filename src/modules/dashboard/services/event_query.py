"""事件历史查询面：内存环形缓冲 ∪ 诊断事件流文件。

事件日志页"加载更早 / 时间跳转"的数据源。内存环形缓冲（5000 条）只覆盖
本次运行的观察窗，跨启动的完整事实源是诊断事件流文件（``logs/events``
逐小时 JSONL）。本读取面把两个来源按记录 id 去重合并成一根
时间轴，沿读取方向凑满一页即停；环形缓冲全覆盖时不扫文件。

与 ``SessionEventReader``（控制台回看）的差异：不做场次过滤、不做卡面
白名单——事件页的语义是全量总线事件流诊断面。诊断文件缺失/损坏一律
按"无数据"跳过并如实回报（``files_pruned``），绝不抛错阻断。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from src.modules.events.event_history import EventRecord
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer
    from src.modules.events.event_history import EventHistoryService

logger = get_logger("EventQueryReader")

# 扫描的天数上界：防异常大的时间窗把日期枚举拖成灾
_MAX_SCAN_DAYS = 370


def events_dir_of(server: "DashboardServer") -> Optional[Path]:
    """从装配取诊断事件流目录（logs/events）；诊断未装配时返回 None（降级只读环形缓冲）。"""
    writer = getattr(server, "diagnostics_writer", None)
    if writer is None:
        return None
    return writer.base_dir / "events"


@dataclass
class EventQueryWindow:
    """一次事件查询的结果（升序、双向续翻信号）。"""

    records: List[EventRecord] = field(default_factory=list)
    has_more_older: bool = False
    has_more_newer: bool = False
    files_pruned: bool = False


class EventQueryReader:
    """合并环形缓冲与诊断文件的全量事件窗口查询器。

    两个来源可任缺其一：``events_dir`` 为 None（诊断未装配）时只读环形
    缓冲，``event_history`` 为 None（极简装配）时只读文件。
    """

    def __init__(
        self,
        event_history: Optional["EventHistoryService"] = None,
        events_dir: Optional[Path] = None,
    ) -> None:
        self.event_history = event_history
        self.events_dir = events_dir

    def query(
        self,
        *,
        start_ms: int,
        end_ms: int,
        before_timestamp_ms: Optional[int] = None,
        types: Optional[List[str]] = None,
        level: Optional[str] = None,
        take: str = "newest",
        limit: int = 200,
    ) -> EventQueryWindow:
        """查询窗口内的事件记录（时间升序返回）。

        ``take="newest"`` 取窗口内最靠新的一页（默认，"加载更早"向前翻页）；
        ``take="oldest"`` 取最靠旧的一页（时间跳转后向后阅读）。环形缓冲
        全量取出后过滤（容量 ≤5000，成本可忽略），文件侧只扫环形覆盖不到
        的更早年代，沿读取方向凑满一页即停。
        """
        if limit <= 0:
            return EventQueryWindow()

        ring = sorted(
            (
                record
                for record in self._ring_candidates(types, level, before_timestamp_ms)
                if start_ms <= record.timestamp_ms < end_ms
            ),
            key=lambda record: record.timestamp_ms,
        )
        # 文件侧只负责比环形最老记录更早的年代（环形为空则整个窗口），
        # 避免与环形重复；before 游标进一步收口
        bounds = [bound for bound in (before_timestamp_ms, ring[0].timestamp_ms if ring else None) if bound is not None]
        file_upper = min(bounds) if bounds else end_ms

        if take != "oldest":
            return self._page_newest(ring, file_upper, start_ms=start_ms, types=types, level=level, limit=limit)
        return self._page_oldest(ring, file_upper, start_ms=start_ms, types=types, level=level, limit=limit)

    # ------------------------------------------------------------------ #
    # 内部实现                                                            #
    # ------------------------------------------------------------------ #

    def _ring_candidates(
        self,
        types: Optional[List[str]],
        level: Optional[str],
        before_timestamp_ms: Optional[int],
    ) -> List[EventRecord]:
        """环形缓冲候选（含 before 游标过滤；无环形时返回空）。"""
        if self.event_history is None:
            return []
        return self.event_history.query(
            types=types,
            level=level,
            before_timestamp_ms=before_timestamp_ms,
            limit=10_000,
        )

    def _page_newest(
        self,
        ring: List[EventRecord],
        file_upper: int,
        *,
        start_ms: int,
        types: Optional[List[str]],
        level: Optional[str],
        limit: int,
    ) -> EventQueryWindow:
        """取最靠新的一页：环形够页直接截取，不足补扫文件（新→旧）。"""
        if len(ring) >= limit:
            page = ring[-limit:]
            has_more_older = len(ring) > limit
            if not has_more_older:
                # 环形恰好取尽：更早命中只可能在文件年代，探测一锤定音
                has_more_older = self._scan_files(
                    upper_ms=page[0].timestamp_ms,
                    start_ms=start_ms,
                    types=types,
                    level=level,
                    need=0,
                    newest_first=True,
                )[1]
            return EventQueryWindow(records=page, has_more_older=has_more_older)

        file_hits, stopped_early, files_pruned = self._scan_files(
            upper_ms=file_upper,
            start_ms=start_ms,
            types=types,
            level=level,
            need=limit - len(ring),
            newest_first=True,
        )
        # 文件年代严格早于环形年代；文件侧收集顺序不保证整体升序，排序后合并
        merged = sorted(file_hits + ring, key=lambda record: record.timestamp_ms)
        return EventQueryWindow(
            records=merged[-limit:],
            has_more_older=stopped_early,
            files_pruned=files_pruned,
        )

    def _page_oldest(
        self,
        ring: List[EventRecord],
        file_upper: int,
        *,
        start_ms: int,
        types: Optional[List[str]],
        level: Optional[str],
        limit: int,
    ) -> EventQueryWindow:
        """取最靠旧的一页：文件侧（更早年代）旧→新凑页，不足由环形补齐。"""
        file_hits, stopped_early, files_pruned = self._scan_files(
            upper_ms=file_upper,
            start_ms=start_ms,
            types=types,
            level=level,
            need=limit,
            newest_first=False,
        )
        merged = sorted(file_hits + ring, key=lambda record: record.timestamp_ms)
        return EventQueryWindow(
            records=merged[:limit],
            has_more_newer=stopped_early or len(merged) > limit,
            files_pruned=files_pruned,
        )

    def _scan_files(
        self,
        *,
        upper_ms: int,
        start_ms: int,
        types: Optional[List[str]],
        level: Optional[str],
        need: int,
        newest_first: bool,
    ) -> tuple[List[EventRecord], bool, bool]:
        """扫描诊断文件年代（严格早于 upper_ms），沿读取方向收集至 need+1 条。

        返回 (命中, 是否提前凑满, 天目录缺失信号)；提前凑满即该方向还有更多
        （need=0 时退化为存在性探测）。小时文件缺失属正常（安静小时无文件），
        只有天目录整体缺失才计为 pruned（与 SessionEventReader 口径一致）。
        """
        if self.events_dir is None:
            return [], False, False
        if not self.events_dir.is_dir():
            return [], False, True

        collected: List[EventRecord] = []
        stopped_early = False
        files_pruned = False
        for day in self._window_days(start_ms, upper_ms, newest_first):
            day_dir = self.events_dir / day.strftime("%Y-%m-%d")
            if not day_dir.is_dir():
                files_pruned = True
                continue
            for hour_file in sorted(day_dir.glob("*.jsonl"), reverse=newest_first):
                collected.extend(
                    self._read_hour_file(hour_file, start_ms=start_ms, upper_ms=upper_ms, types=types, level=level)
                )
                if len(collected) > need:
                    stopped_early = True
                    break
            if stopped_early:
                break
        # 天间按读取方向倒走、文件内行序恒为升序——候选集是"沿读取方向最先
        # 遇到的条目"，排序交给合并侧（_page_newest/_page_oldest）
        return collected, stopped_early, files_pruned

    @staticmethod
    def _window_days(start_ms: int, upper_ms: int, newest_first: bool) -> List[datetime]:
        """窗口覆盖的日期序列（按读取方向排序，超出上界则截断）。"""
        first_day = datetime.fromtimestamp(start_ms / 1000).date()
        last_day = datetime.fromtimestamp(max(upper_ms - 1, start_ms) / 1000).date()
        if (last_day - first_day).days > _MAX_SCAN_DAYS:
            last_day = first_day + timedelta(days=_MAX_SCAN_DAYS)
            logger.warning(f"事件查询时间跨度超过 {_MAX_SCAN_DAYS} 天，扫描截断至该上界")
        all_days = [
            datetime.combine(first_day + timedelta(days=offset), datetime.min.time())
            for offset in range((last_day - first_day).days + 1)
        ]
        return all_days[::-1] if newest_first else all_days

    def _read_hour_file(
        self,
        path: Path,
        *,
        start_ms: int,
        upper_ms: int,
        types: Optional[List[str]],
        level: Optional[str],
    ) -> List[EventRecord]:
        """解析单小时文件的窗口内命中（升序）；损坏行跳过计数。"""
        hits: List[EventRecord] = []
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
                        ts = int(row.get("timestamp_ms", 0))
                        if not (start_ms <= ts < upper_ms):
                            continue
                        if types is not None and str(row.get("type", "")) not in types:
                            continue
                        if level is not None and str(row.get("level", "")) != level:
                            continue
                        hits.append(EventRecord.model_validate(row))
                    except Exception:
                        malformed += 1
        except OSError as e:
            logger.warning(f"读取诊断事件文件失败，跳过: {path} ({e})")
            return hits
        if malformed:
            logger.debug(f"诊断事件文件存在无法解析的行（已跳过 {malformed} 行）: {path}")
        return hits


__all__ = [
    "EventQueryReader",
    "EventQueryWindow",
    "events_dir_of",
]
