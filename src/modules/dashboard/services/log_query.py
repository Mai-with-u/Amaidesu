"""日志落盘查询面：``logs/webui`` 按天 JSONL 的时间窗检索。

日志查看器"加载更早 / 时间跳转 / 服务端搜索"的数据源。落盘文件由
LogStreamer 按天写（``logs/webui/YYYY-MM-DD.jsonl``，默认仅 INFO 及以上
——DEBUG 只存在于内存尾部缓冲，重启即失），本读取面逐行解析、按窗口与
筛选条件过滤，沿读取方向从靠新一侧向靠旧一侧（或反之）倒走，凑满一页
即停，避免全量扫描。

落盘数据是可删除的旁路（用户手动管理空间）：目录缺失、文件损坏、单行
解析失败一律按"无数据"跳过并如实回报（``days_missing``），查询降级为空，
绝不抛错阻断。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Set

from src.modules.logging import get_logger

logger = get_logger("LogHistoryReader")

# 扫描的天数上界：防异常大的时间窗把日期枚举拖成灾
_MAX_SCAN_DAYS = 370


@dataclass
class LogQueryWindow:
    """一次日志查询的结果（升序、双向续翻信号）。"""

    entries: List[dict[str, Any]] = field(default_factory=list)
    has_more_older: bool = False
    has_more_newer: bool = False
    days_missing: bool = False


class LogHistoryReader:
    """合并落盘文件的日志窗口查询器（纯文件读取，无内存缓冲参与）。"""

    def __init__(self, persist_dir: Optional[Path]) -> None:
        self.persist_dir = persist_dir

    @property
    def persist_available(self) -> bool:
        """落盘目录存在即可查询（未启用落盘/目录被清时 False）。"""
        return self.persist_dir is not None and self.persist_dir.is_dir()

    def query(
        self,
        *,
        start_ms: int,
        end_ms: int,
        before_timestamp_ms: Optional[int] = None,
        levels: Optional[Set[str]] = None,
        modules: Optional[Set[str]] = None,
        keyword: Optional[str] = None,
        take: str = "newest",
        limit: int = 200,
    ) -> LogQueryWindow:
        """查询窗口内的日志条目（时间升序返回）。

        ``take="newest"`` 取窗口内最靠新的一页（默认，"加载更早"向前翻页）；
        ``take="oldest"`` 取最靠旧的一页（时间跳转后向后阅读）。
        ``before_timestamp_ms`` 游标只保留严格早于该时刻的条目。
        ``has_more_older/newer`` 指示沿对应方向是否还有命中条目。
        """
        if not self.persist_available or limit <= 0:
            return LogQueryWindow()

        newest_first = take != "oldest"
        days = self._window_days(start_ms, end_ms, newest_first)

        collected: List[dict[str, Any]] = []
        stopped_early = False
        days_missing = False
        for day in days:
            day_file = self.persist_dir / f"{day.strftime('%Y-%m-%d')}.jsonl"  # type: ignore[union-attr]
            if not day_file.exists():
                days_missing = True
                continue
            day_hits = self._read_day_file(
                day_file,
                start_ms=start_ms,
                end_ms=end_ms,
                before_timestamp_ms=before_timestamp_ms,
                levels=levels,
                modules=modules,
                keyword=keyword,
            )
            collected.extend(day_hits)
            if len(collected) >= limit:
                stopped_early = True
                break

        # 天间按读取方向倒走、天内行序恒为升序——候选集是"沿读取方向最先
        # 遇到的 limit 条"，按时间戳排序后从靠新一侧（newest）或靠旧一侧
        # （oldest）截取一页，返回统一为时间升序
        ordered = sorted(collected, key=lambda entry: entry["timestamp"])
        page = ordered[-limit:] if newest_first else ordered[:limit]
        return LogQueryWindow(
            entries=page,
            has_more_older=stopped_early if newest_first else False,
            has_more_newer=stopped_early if not newest_first else False,
            days_missing=days_missing,
        )

    # ------------------------------------------------------------------ #
    # 内部实现                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _window_days(start_ms: int, end_ms: int, newest_first: bool) -> List[datetime]:
        """窗口覆盖的日期序列（按读取方向排序，超出上界则截断）。"""
        first_day = datetime.fromtimestamp(start_ms / 1000).date()
        last_day = datetime.fromtimestamp(end_ms / 1000).date()
        if (last_day - first_day).days > _MAX_SCAN_DAYS:
            last_day = first_day + timedelta(days=_MAX_SCAN_DAYS)
            logger.warning(f"日志查询时间跨度超过 {_MAX_SCAN_DAYS} 天，扫描截断至该上界")
        all_days = [
            datetime.combine(first_day + timedelta(days=offset), datetime.min.time())
            for offset in range((last_day - first_day).days + 1)
        ]
        return all_days[::-1] if newest_first else all_days

    def _read_day_file(
        self,
        path: Path,
        *,
        start_ms: int,
        end_ms: int,
        before_timestamp_ms: Optional[int],
        levels: Optional[Set[str]],
        modules: Optional[Set[str]],
        keyword: Optional[str],
    ) -> List[dict[str, Any]]:
        """解析单天文件，返回窗口内命中条目（文件序 = 时间升序）。"""
        hits: List[dict[str, Any]] = []
        malformed = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = self._parse_line(line)
                        if entry is None:
                            raise ValueError("日志行缺少必需字段")
                        ts_ms = self._ts_of(entry["timestamp"])
                        if ts_ms is None or not (start_ms <= ts_ms < end_ms):
                            continue
                        if before_timestamp_ms is not None and ts_ms >= before_timestamp_ms:
                            continue
                        if levels is not None and entry["level"].upper() not in levels:
                            continue
                        if modules is not None and entry["module"] not in modules:
                            continue
                        if keyword is not None:
                            folded = keyword.casefold()
                            if folded not in entry["message"].casefold() and folded not in entry["module"].casefold():
                                continue
                        hits.append(entry)
                    except Exception:
                        malformed += 1
        except OSError as e:
            logger.warning(f"读取日志落盘文件失败，跳过: {path} ({e})")
            return hits
        if malformed:
            logger.debug(f"日志落盘文件存在无法解析的行（已跳过 {malformed} 行）: {path}")
        return hits

    @staticmethod
    def _parse_line(line: str) -> Optional[dict[str, Any]]:
        """单行 JSON → 日志条目 dict；缺必需字段返回 None（调用方按坏行计）。"""
        row = json.loads(line)
        if not isinstance(row, dict):
            return None
        if not all(isinstance(row.get(key), str) for key in ("timestamp", "level", "module", "message")):
            return None
        return row

    @staticmethod
    def _ts_of(iso_timestamp: str) -> Optional[int]:
        """ISO 本地时间 → Unix 毫秒；解析失败返回 None。"""
        try:
            return int(datetime.fromisoformat(iso_timestamp).timestamp() * 1000)
        except (ValueError, OSError, OverflowError):
            return None


__all__ = [
    "LogHistoryReader",
    "LogQueryWindow",
]
