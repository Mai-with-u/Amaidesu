"""录制回放引擎：把 EventHistory 录制的世界快照按原节奏重新发射。

录制源是 ``data/events/YYYY-MM-DD.jsonl``（EventHistoryService 全量事件落盘），
本引擎过滤出 ``room.message.danmaku`` 事件、还原原始 payload，按相邻消息的
毫秒时间戳差值调度重放。回放消息统一携带 ``simulated=True`` 溯源标记——
它们是"被重新注入的输入流"，不进真实数据统计。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from src.modules.events.event_history import read_day_events
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import get_logger
from src.modules.simulator.config_schema import SimulatorConfigSchema


class ReplayEngine:
    """单日录制回放队列。

    生命周期：``load(date)`` 构建队列 → 调用方逐条 ``pop_next()`` 并按
    ``next_gap_seconds()`` 的间隔调度 emit → 队列空即回放结束。
    """

    def __init__(self, config: SimulatorConfigSchema, persist_dir: Optional[Path] = None) -> None:
        self._config = config
        self._persist_dir = persist_dir
        self.logger = get_logger("ReplayEngine")
        self._queue: List[RoomMessagePayload] = []
        self._cursor = 0
        self._replay_date: Optional[str] = None

    @property
    def replay_date(self) -> Optional[str]:
        """当前加载的录制日期（未加载为 None）。"""
        return self._replay_date

    @property
    def remaining(self) -> int:
        """尚未回放的消息条数。"""
        return len(self._queue) - self._cursor

    @property
    def total(self) -> int:
        """本次回放加载的总条数。"""
        return len(self._queue)

    def load(self, date_str: str, *, simulated_only: Optional[bool] = None) -> int:
        """读取指定日期录制并构建回放队列，返回加载条数。

        Args:
            date_str: 录制日期（``YYYY-MM-DD``）
            simulated_only: 是否仅回放录制时已标记 simulated 的消息；
                None 时用配置的 ``replay_simulated_only``。
        """
        if simulated_only is None:
            simulated_only = self._config.replay_simulated_only

        entries: List[RoomMessagePayload] = []
        for record in read_day_events(date_str, self._persist_dir):
            if record.type != "room.message.danmaku":
                continue
            # 兼容旧录制（场次主键化前 live_session_id 为房间字符串）：统一清零，
            # 回放事件经场次盖章拦截器归属到当前回放场次
            data = dict(record.data)
            if not isinstance(data.get("live_session_id"), int):
                data["live_session_id"] = 0
            try:
                payload = RoomMessagePayload.model_validate(data)
            except Exception as exc:
                self.logger.debug(f"回放跳过无法解析的录制记录: {exc}")
                continue
            if simulated_only and not payload.simulated:
                continue
            if not payload.content:
                continue
            entries.append(payload)

        # 文件顺序即时间正序，这里仅防御乱序录制：按 timestamp_ms 稳定排序
        entries.sort(key=lambda p: p.timestamp_ms)
        self._queue = entries
        self._cursor = 0
        self._replay_date = date_str if entries else None
        self.logger.info(f"回放队列已加载: date={date_str} 条数={len(self._queue)} simulated_only={simulated_only}")
        return len(self._queue)

    def next_gap_seconds(self) -> float:
        """距下一条消息的调度间隔（秒）。

        由相邻消息的 ``timestamp_ms`` 差值除以回放速度得到；首条消息无前驱
        间隔为 0；超长冷场按 ``replay_gap_cap_s`` 截断。
        """
        if self._cursor >= len(self._queue):
            return 0.0
        if self._cursor == 0:
            return 0.0
        gap_ms = self._queue[self._cursor].timestamp_ms - self._queue[self._cursor - 1].timestamp_ms
        gap_s = max(0.0, gap_ms / 1000.0) / max(self._config.replay_speed, 0.1)
        return min(gap_s, self._config.replay_gap_cap_s)

    def pop_next(self) -> Optional[RoomMessagePayload]:
        """弹出下一条待回放消息；队列耗尽返回 None。"""
        if self._cursor >= len(self._queue):
            return None
        payload = self._queue[self._cursor]
        self._cursor += 1
        return payload


__all__ = ["ReplayEngine"]
