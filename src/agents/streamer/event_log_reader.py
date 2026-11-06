"""诊断事件流文件的窗口读取器（续播重放的数据源）。

事件全量落盘事实源是 ``DiagnosticsWriter`` 的诊断事件流（``logs/events/
YYYY-MM-DD/HH.jsonl``，每行一条 ``EventRecord`` 序列化）。本模块只做一件事：
给定事件名与毫秒时间窗，把窗口内的记录行按时间升序读回来。目录缺失、文件
缺失、单行损坏一律按"无数据"跳过——重放是尽力而为的补偿，绝不抛错阻断开播。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

from src.modules.logging import get_logger

logger = get_logger("EventLogReader")

# 扫描的天数上界：防异常大的时间窗把日期枚举拖成灾（与回看读取面同口径）
_MAX_SCAN_DAYS = 370


class EventLogFileReader:
    """诊断事件流文件的按窗口读取器。非线程安全；读取是同步磁盘 IO（低频路径）。"""

    def __init__(self, events_dir: Path) -> None:
        """
        Args:
            events_dir: 诊断事件流目录（即 DiagnosticsWriter 的 ``<base_dir>/events``）
        """
        self._events_dir = Path(events_dir)

    def read_records(self, *, event_name: str, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
        """读回 ``[start_ms, end_ms)`` 窗口内指定事件名的记录行（时间升序）。

        每行是 EventRecord 的原始 dict（含 ``event_name`` / ``timestamp_ms`` /
        ``data`` 载荷字典）；``start_ms >= end_ms`` 返回空表。
        """
        if start_ms >= end_ms or not self._events_dir.is_dir():
            return []
        first_day = datetime.fromtimestamp(start_ms / 1000).date()
        last_day = datetime.fromtimestamp((end_ms - 1) / 1000).date()
        if (last_day - first_day).days > _MAX_SCAN_DAYS:
            last_day = first_day + timedelta(days=_MAX_SCAN_DAYS)
            logger.warning(f"事件读取时间跨度超过 {_MAX_SCAN_DAYS} 天，扫描截断至该上界")

        hits: List[Dict[str, Any]] = []
        day = first_day
        while day <= last_day:
            day_dir = self._events_dir / day.strftime("%Y-%m-%d")
            # 小时文件缺失属正常（安静小时无文件），天目录缺失同样静默跳过
            if day_dir.is_dir():
                for hour_file in sorted(day_dir.glob("*.jsonl")):
                    hits.extend(
                        self._read_hour_file(hour_file, event_name=event_name, start_ms=start_ms, end_ms=end_ms)
                    )
            day += timedelta(days=1)
        hits.sort(key=lambda row: int(row.get("timestamp_ms", 0)))
        return hits

    @staticmethod
    def _read_hour_file(path: Path, *, event_name: str, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
        """解析单小时文件窗口内的指定事件行（升序）；损坏行跳过并计数。"""
        hits: List[Dict[str, Any]] = []
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
                        if str(row.get("event_name", "")) != event_name:
                            continue
                        ts = int(row.get("timestamp_ms", 0))
                        if not (start_ms <= ts < end_ms):
                            continue
                        hits.append(row)
                    except Exception:
                        malformed += 1
        except OSError as e:
            logger.warning(f"读取诊断事件文件失败，跳过: {path} ({e})")
            return hits
        if malformed:
            logger.debug(f"诊断事件文件存在无法解析的行（已跳过 {malformed} 行）: {path}")
        return hits


__all__ = ["EventLogFileReader"]
